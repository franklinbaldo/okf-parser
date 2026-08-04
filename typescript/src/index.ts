export {
  DocumentParseError,
  ExclusionFileError,
  ExclusionRules,
  OkfParserError,
  checkBundle,
  conceptId,
  discoverMarkdown,
  graphBundle,
  hasMarkdownSuffix,
  inventoryBundle,
  isMarkdownFilename,
  isReservedDocument,
  iterHeadings,
  iterMarkdownLinks,
  loadBundle,
  looksLikeFrontmatterLink,
  parseDocument,
  parseDocumentContent,
  resolveLocalTarget,
  splitOptionalFrontmatter,
} from "./core.js";
export type {
  Bundle,
  CheckOptions,
  CheckReport,
  ConceptRecord,
  Diagnostic,
  FrontmatterValue,
  GraphReport,
  InventoryReport,
  JsonValue,
  LinkRecord,
  LoadOptions,
  ParsedDocument,
  ReservedRecord,
  Severity,
} from "./core.js";
export {
  formatMarkdown,
  formatPath,
  protectedBlockSignature,
  safeFormatMarkdown,
} from "./formatting.js";
export type {
  FormatOptions,
  FormatReport,
  ProtectedAttribute,
  ProtectedBlockRecord,
} from "./formatting.js";
export {
  canClassifyAs,
  classifyLexemes,
  isIsoDateLexeme,
  isIsoDateTimeLexeme,
} from "./lexemes.js";
export type { LexemeKind } from "./lexemes.js";
export { createMcpServer } from "./mcp.js";
export {
  SchemaCastError,
  SchemaExportError,
  SchemaNameCollisionError,
  exportJsonSchema,
  exportZod,
} from "./schema.js";
export type {
  CastKind,
  JsonSchema,
  SchemaOptions,
  SchemaReport,
  ZodImport,
  ZodOptions,
} from "./schema.js";
export {
  SPEC_SLUG_PLACEHOLDER,
  missingTypeSpecs,
  specRelativePath,
  typeSlug,
} from "./type-specs.js";
export { PROTOCOL_VERSION } from "./version.js";
import { PROTOCOL_VERSION } from "./version.js";

export const capabilityManifest = Object.freeze({
  protocol_version: PROTOCOL_VERSION,
  capabilities: Object.freeze({
    check: "stable",
    inventory: "stable",
    graph: "stable",
    schema_json: "stable",
    schema_zod: "stable",
    format: "stable",
    duckdb: "not_implemented",
    mcp: "stable",
  }),
});
