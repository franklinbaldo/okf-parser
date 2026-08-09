import path from "node:path";

import {
  type Bundle,
  type FrontmatterValue,
  type LoadOptions,
  OkfParserError,
  loadBundle,
} from "./core.js";
import {
  type LexemeKind,
  canClassifyAs,
  classifyLexemes,
} from "./lexemes.js";

export type CastKind = LexemeKind;
export type ZodImport = "zod" | "astro";

export interface DeclaredLogicalType {
  readonly sql: string;
  readonly family: "string" | "boolean" | "integer" | "float" | "decimal" | "date" | "timestamp" | "timestamptz" | "uuid" | "list" | "other";
  readonly element?: DeclaredLogicalType;
  readonly precision?: number;
  readonly scale?: number;
  readonly isJsSafeInteger?: boolean;
}

export type DeclaredTypesByType = Readonly<
  Record<string, Readonly<Record<string, DeclaredLogicalType>>>
>;

export interface SchemaOptions extends LoadOptions {
  readonly inferTypes?: boolean;
  readonly casts?: readonly string[];
  readonly declaredTypesByType?: DeclaredTypesByType;
}

export interface ZodOptions extends SchemaOptions {
  readonly zodImport?: ZodImport;
}

export interface SchemaReport {
  readonly root: string;
  readonly total_types: number;
  readonly inferred_types: boolean;
  readonly casts: readonly string[];
  readonly schemas: Readonly<Record<string, JsonSchema>>;
}

export type JsonSchema = Readonly<Record<string, unknown>>;

export type ScalarKind = CastKind;

export type ContractNode =
  | { readonly kind: "scalar"; readonly scalar: ScalarKind; readonly declaredType?: DeclaredLogicalType }
  | { readonly kind: "literal"; readonly value: string }
  | { readonly kind: "object"; readonly fields: readonly FieldContract[] }
  | { readonly kind: "list"; readonly item: ContractNode; readonly itemNullable: boolean; readonly declaredType?: DeclaredLogicalType }
  | { readonly kind: "any" };

export interface FieldContract {
  readonly name: string;
  readonly required: boolean;
  readonly nullable: boolean;
  readonly value: ContractNode;
}

export interface TypeContract {
  readonly conceptType: string;
  readonly modelName: string;
  readonly root: Extract<ContractNode, { readonly kind: "object" }>;
}

interface CompileOptions {
  readonly inferTypes: boolean;
  readonly casts: ReadonlyMap<string, CastKind>;
  readonly declaredTypesByType: DeclaredTypesByType;
  readonly usedCasts: Set<string>;
}

const CAST_KINDS = new Set<CastKind>([
  "string",
  "boolean",
  "integer",
  "number",
  "date",
  "datetime",
]);
const IDENTIFIER_RE = /^[$_\p{ID_Start}][$\u200C\u200D_\p{ID_Continue}]*$/u;

export class SchemaExportError extends OkfParserError {
  constructor(code: string, message: string) {
    super(code, message);
  }
}

export class SchemaCastError extends SchemaExportError {
  constructor(message: string) {
    super("OKF_SCHEMA_CAST", message);
  }
}

export class SchemaNameCollisionError extends SchemaExportError {
  constructor(message: string) {
    super("OKF_SCHEMA_NAME_COLLISION", message);
  }
}

function parseCasts(specifications: readonly string[]): Map<string, CastKind> {
  const casts = new Map<string, CastKind>();
  for (const specification of specifications) {
    const separator = specification.lastIndexOf("=");
    const field = separator >= 0 ? specification.slice(0, separator).trim() : "";
    const rawKind = separator >= 0 ? specification.slice(separator + 1).trim().toLowerCase() : "";
    if (field === "" || !CAST_KINDS.has(rawKind as CastKind)) {
      throw new SchemaCastError(
        `invalid cast ${JSON.stringify(specification)}; expected FIELD=TYPE, where TYPE is ${[
          ...CAST_KINDS,
        ]
          .sort()
          .join(", ")}`,
      );
    }
    const kind = rawKind as CastKind;
    const previous = casts.get(field);
    if (previous !== undefined && previous !== kind) {
      throw new SchemaCastError(`field ${JSON.stringify(field)} has conflicting casts: ${previous} and ${kind}`);
    }
    casts.set(field, kind);
  }
  return casts;
}

function isRecord(value: FrontmatterValue): value is Readonly<Record<string, FrontmatterValue>> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function freezeDeclaredLogicalType(value: DeclaredLogicalType): DeclaredLogicalType {
  return Object.freeze({
    ...value,
    ...(value.element === undefined ? {} : { element: freezeDeclaredLogicalType(value.element) }),
  });
}

function freezeContractNode<T extends ContractNode>(node: T): T {
  if (node.kind === "object") {
    for (const field of node.fields) {
      freezeContractNode(field.value);
      Object.freeze(field);
    }
    Object.freeze(node.fields);
  } else if (node.kind === "list") {
    freezeContractNode(node.item);
  }
  return Object.freeze(node);
}

function declaredNode(declaredType: DeclaredLogicalType): ContractNode {
  const immutableDeclaredType = freezeDeclaredLogicalType(declaredType);
  if (immutableDeclaredType.family === "list" && immutableDeclaredType.element !== undefined) {
    return {
      kind: "list",
      item: declaredNode(immutableDeclaredType.element),
      itemNullable: false,
      declaredType: immutableDeclaredType,
    };
  }
  const scalar: ScalarKind =
    immutableDeclaredType.family === "boolean" ? "boolean" :
    immutableDeclaredType.family === "integer" ? "integer" :
    immutableDeclaredType.family === "float" || immutableDeclaredType.family === "decimal" ? "number" :
    immutableDeclaredType.family === "date" ? "date" :
    immutableDeclaredType.family === "timestamp" || immutableDeclaredType.family === "timestamptz" ? "datetime" : "string";
  return { kind: "scalar", scalar, declaredType: immutableDeclaredType };
}

function scalarContract(values: readonly string[], fieldPath: string, options: CompileOptions, conceptType?: string): ContractNode {
  const explicit = options.casts.get(fieldPath);
  if (explicit !== undefined) {
    options.usedCasts.add(fieldPath);
    if (!canClassifyAs(values, explicit)) {
      const sample = values.find((value) => !canClassifyAs([value], explicit)) ?? values[0] ?? "";
      throw new SchemaCastError(
        `cannot cast ${JSON.stringify(fieldPath)} to ${explicit}: ${JSON.stringify(sample)} is incompatible`,
      );
    }
    return { kind: "scalar", scalar: explicit };
  }
  const declared = conceptType === undefined ? undefined : options.declaredTypesByType[conceptType]?.[fieldPath];
  if (declared !== undefined) return declaredNode(declared);
  return { kind: "scalar", scalar: options.inferTypes ? classifyLexemes(values) : "string" };
}

function compileValue(
  values: readonly FrontmatterValue[],
  fieldPath: string,
  options: CompileOptions,
  conceptType?: string,
): ContractNode {
  const declared = conceptType === undefined || options.casts.has(fieldPath)
    ? undefined
    : options.declaredTypesByType[conceptType]?.[fieldPath];
  if (declared !== undefined) {
    const node = declaredNode(declared);
    if (node.kind === "list") {
      const items = values.filter(Array.isArray).flatMap((value) => value);
      return { ...node, itemNullable: items.some((item) => item === null) };
    }
    return node;
  }
  const nonNull = values.filter((value): value is Exclude<FrontmatterValue, null> => value !== null);
  if (nonNull.length === 0) return scalarContract([], fieldPath, options, conceptType);

  if (nonNull.every(isRecord)) {
    if (options.casts.has(fieldPath)) {
      throw new SchemaCastError(`cannot cast ${JSON.stringify(fieldPath)}: the field contains objects`);
    }
    return compileObject(nonNull, fieldPath, options);
  }

  if (nonNull.every(Array.isArray)) {
    const items = nonNull.flatMap((value) => value);
    const itemNullable = items.some((item) => item === null);
    const item = compileValue(items, fieldPath, options);
    return { kind: "list", item, itemNullable };
  }

  if (nonNull.some((value) => Array.isArray(value) || isRecord(value))) {
    if (options.casts.has(fieldPath)) {
      throw new SchemaCastError(
        `cannot cast ${JSON.stringify(fieldPath)}: the field mixes scalar and structured values`,
      );
    }
    return { kind: "any" };
  }

  return scalarContract(nonNull.map(String), fieldPath, options, conceptType);
}

function compileObject(
  documents: readonly Readonly<Record<string, FrontmatterValue>>[],
  parentPath: string,
  options: CompileOptions,
  conceptType?: string,
): Extract<ContractNode, { readonly kind: "object" }> {
  const keys = new Set(documents.flatMap((document) => Object.keys(document)));
  if (conceptType !== undefined) {
    keys.add("type");
    keys.add("title");
    keys.add("description");
    for (const declaredField of Object.keys(options.declaredTypesByType[conceptType] ?? {})) keys.add(declaredField);
  }
  const fields: FieldContract[] = [];
  for (const name of [...keys].sort((left, right) => left.localeCompare(right, "en"))) {
    const fieldPath = parentPath === "" ? name : `${parentPath}.${name}`;
    const present = documents.filter((document) => Object.hasOwn(document, name)).map((document) => document[name]);
    const required = name === "type" && conceptType !== undefined ? true : present.length === documents.length;
    const nullable = name === "type" && conceptType !== undefined ? false : present.some((value) => value === null);
    const value =
      name === "type" && conceptType !== undefined
        ? ({ kind: "literal", value: conceptType } as const)
        : compileValue(present.filter((item): item is FrontmatterValue => item !== undefined), fieldPath, options, conceptType);
    fields.push(Object.freeze({ name, required, nullable, value }));
  }
  return { kind: "object", fields: Object.freeze(fields) };
}

function identifierName(value: string, suffix: string): string {
  const normalized = value.normalize("NFKC");
  const replaced = [...normalized]
    .map((character) => /[\p{L}\p{N}_]/u.test(character) ? character : "_")
    .join("")
    .replace(/^_+|_+$/gu, "");
  const parts = replaced.split("_").filter(Boolean);
  let name = parts.map((part) => `${[...part][0]?.toUpperCase() ?? ""}${[...part].slice(1).join("")}`).join("");
  if (name === "") name = "Concept";
  if (/^\p{N}/u.test(name)) name = `Concept${name}`;
  if (!IDENTIFIER_RE.test(name)) {
    const encoded = [...normalized].map((character) => `U${character.codePointAt(0)?.toString(16).toUpperCase().padStart(4, "0")}`).join("");
    name = encoded === "" ? "Concept" : `Concept${encoded}`;
  }
  return `${name}${suffix}`;
}

function uniqueNames(values: readonly string[], suffix: string): Map<string, string> {
  const names = new Map<string, string>();
  const owners = new Map<string, string>();
  for (const value of [...values].sort((left, right) => left.localeCompare(right, "en"))) {
    const name = identifierName(value, suffix);
    const previous = owners.get(name);
    if (previous !== undefined && previous !== value) {
      throw new SchemaNameCollisionError(
        `concept types ${JSON.stringify(previous)} and ${JSON.stringify(value)} both normalize to ${JSON.stringify(name)}`,
      );
    }
    names.set(value, name);
    owners.set(name, value);
  }
  return names;
}

function parseFrontmatterJson(value: string): Readonly<Record<string, FrontmatterValue>> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new SchemaExportError("OKF_SCHEMA_FRONTMATTER", "serialized frontmatter is not an object");
  }
  return parsed as Readonly<Record<string, FrontmatterValue>>;
}

export function compileBundleTypeContracts(bundle: Bundle, schemaOptions: SchemaOptions = {}): readonly TypeContract[] {
  const specifications = schemaOptions.casts ?? [];
  const options: CompileOptions = {
    inferTypes: schemaOptions.inferTypes ?? false,
    casts: parseCasts(specifications),
    declaredTypesByType: schemaOptions.declaredTypesByType ?? {},
    usedCasts: new Set(),
  };
  const byType = new Map<string, Array<Readonly<Record<string, FrontmatterValue>>>>();
  for (const concept of bundle.concepts) {
    const documents = byType.get(concept.conceptType) ?? [];
    documents.push(parseFrontmatterJson(concept.frontmatterJson));
    byType.set(concept.conceptType, documents);
  }
  const names = uniqueNames([...byType.keys()], "Concept");
  const contracts = [...byType]
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([conceptType, documents]) =>
      Object.freeze({
        conceptType,
        modelName: names.get(conceptType) ?? identifierName(conceptType, "Concept"),
        root: freezeContractNode(compileObject(documents, "", options, conceptType)),
      }),
    );
  const unused = [...options.casts.keys()].filter((field) => !options.usedCasts.has(field));
  if (unused.length > 0) {
    throw new SchemaCastError(
      `cast field was not found in the bundle: ${unused.sort().map((field) => JSON.stringify(field)).join(", ")}`,
    );
  }
  return Object.freeze(contracts);
}

export async function compileTypeContracts(
  root: string | URL,
  options: SchemaOptions = {},
): Promise<readonly TypeContract[]> {
  const bundle = await loadBundle(root, options);
  return compileBundleTypeContracts(bundle, options);
}

function titleFor(name: string): string {
  return name.length === 0 ? name : `${name[0]?.toUpperCase() ?? ""}${name.slice(1)}`;
}

function declaredScalarSchema(declaredType: DeclaredLogicalType): JsonSchema {
  const schema: Record<string, unknown> = { "x-okf-duckdb-type": declaredType.sql };
  if (declaredType.family === "string") schema.type = "string";
  else if (declaredType.family === "boolean") schema.type = "boolean";
  else if (declaredType.family === "integer") schema.type = "integer";
  else if (declaredType.family === "float" || declaredType.family === "decimal") {
    schema.type = "number";
    if (declaredType.family === "decimal" && declaredType.scale !== undefined && declaredType.scale > 0) schema.multipleOf = Number(`1e-${declaredType.scale}`);
  } else if (declaredType.family === "date") Object.assign(schema, { type: "string", format: "date" });
  else if (declaredType.family === "timestamp") Object.assign(schema, { type: "string", "x-okf-temporal-kind": "timestamp-without-time-zone" });
  else if (declaredType.family === "timestamptz") Object.assign(schema, { type: "string", format: "date-time" });
  else if (declaredType.family === "uuid") Object.assign(schema, { type: "string", format: "uuid" });
  return schema;
}

function scalarSchema(kind: ScalarKind): JsonSchema {
  if (kind === "boolean") return { type: "boolean" };
  if (kind === "integer") return { type: "integer" };
  if (kind === "number") return { type: "number" };
  if (kind === "date") return { type: "string", format: "date" };
  if (kind === "datetime") return { type: "string", format: "date-time" };
  return { type: "string" };
}

function nodeSchema(node: ContractNode): JsonSchema {
  if (node.kind === "scalar") return node.declaredType === undefined ? scalarSchema(node.scalar) : declaredScalarSchema(node.declaredType);
  if (node.kind === "literal") return { type: "string", const: node.value };
  if (node.kind === "any") return {};
  if (node.kind === "list") {
    const item = node.itemNullable
      ? { anyOf: [nodeSchema(node.item), { type: "null" }] }
      : nodeSchema(node.item);
    return { type: "array", items: item, ...(node.declaredType === undefined ? {} : { "x-okf-duckdb-type": node.declaredType.sql }) };
  }
  const properties: Record<string, JsonSchema> = {};
  const required: string[] = [];
  for (const field of node.fields) {
    const base = nodeSchema(field.value);
    properties[field.name] = {
      ...(field.nullable ? { anyOf: [base, { type: "null" }] } : base),
      title: titleFor(field.name),
    };
    if (field.required) required.push(field.name);
  }
  return {
    type: "object",
    properties,
    ...(required.length > 0 ? { required } : {}),
  };
}

export function exportBundleJsonSchema(
  bundle: Bundle,
  options: SchemaOptions = {},
): SchemaReport {
  const contracts = compileBundleTypeContracts(bundle, options);
  const schemas = Object.fromEntries(
    contracts.map((contract) => [
      contract.conceptType,
      { ...nodeSchema(contract.root), title: contract.modelName },
    ]),
  );
  return Object.freeze({
    root: path.resolve(bundle.root),
    total_types: contracts.length,
    inferred_types: options.inferTypes ?? false,
    casts: Object.freeze([...(options.casts ?? [])]),
    schemas: Object.freeze(schemas),
  });
}

export async function exportJsonSchema(
  root: string | URL,
  options: SchemaOptions = {},
): Promise<SchemaReport> {
  const bundle = await loadBundle(root, options);
  return exportBundleJsonSchema(bundle, options);
}

function declaredScalarZod(declaredType: DeclaredLogicalType): string {
  if (declaredType.family === "integer") return declaredType.isJsSafeInteger === false ? "z.union([z.number().int(), z.bigint()])" : "z.number().int()";
  if (declaredType.family === "string") return "z.string()";
  if (declaredType.family === "boolean") return "z.boolean()";
  if (declaredType.family === "float") return "z.number()";
  if (declaredType.family === "decimal") return "z.string()";
  if (declaredType.family === "date") return "z.iso.date()";
  if (declaredType.family === "timestamp") return "z.string()";
  if (declaredType.family === "timestamptz") return "z.iso.datetime({ offset: true })";
  if (declaredType.family === "uuid") return "z.uuid()";
  return "z.unknown()";
}

function scalarZod(kind: ScalarKind): string {
  if (kind === "boolean") return "z.boolean()";
  if (kind === "integer") return "z.number().int()";
  if (kind === "number") return "z.number()";
  if (kind === "date") return "z.iso.date()";
  if (kind === "datetime") return "z.iso.datetime({ offset: true, local: true })";
  return "z.string()";
}

function nodeZod(node: ContractNode, indent = ""): string {
  if (node.kind === "scalar") return node.declaredType === undefined ? scalarZod(node.scalar) : declaredScalarZod(node.declaredType);
  if (node.kind === "literal") return `z.literal(${JSON.stringify(node.value)})`;
  if (node.kind === "any") return "z.unknown()";
  if (node.kind === "list") {
    const item = `${nodeZod(node.item, indent)}${node.itemNullable ? ".nullable()" : ""}`;
    return `z.array(${item})`;
  }
  const childIndent = `${indent}  `;
  const rows = node.fields.map((field) => {
    let rendered = nodeZod(field.value, childIndent);
    if (field.nullable) rendered += ".nullable()";
    if (!field.required) rendered += ".optional()";
    return `${childIndent}${JSON.stringify(field.name)}: ${rendered}`;
  });
  return `z.object({\n${rows.join(",\n")}\n${indent}})`;
}

export async function exportZod(
  root: string | URL,
  options: ZodOptions = {},
): Promise<string> {
  const bundle = await loadBundle(root, options);
  const contracts = compileBundleTypeContracts(bundle, options);
  const variableNames = uniqueNames(
    contracts.map((contract) => contract.conceptType),
    "Schema",
  );
  const importLine =
    (options.zodImport ?? "zod") === "astro"
      ? "import { z } from 'astro:content';"
      : "import { z } from 'zod';";
  const lines = ["// Generated by okf-parser", importLine, ""];
  for (const contract of contracts) {
    lines.push(
      `export const ${variableNames.get(contract.conceptType) ?? identifierName(contract.conceptType, "Schema")} = ${nodeZod(contract.root)};`,
      "",
    );
  }
  return lines.join("\n");
}