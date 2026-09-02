import { DuckDBConnection } from "@duckdb/node-api";
import {
  type Bundle,
  type LoadOptions,
  loadBundle,
} from "@franklinbaldo/okf-parser";

import { loadDeclaredSchemas } from "./declared.js";
import {
  type AttachOptions,
  type ExportReport,
  attachBundle,
} from "./normalized.js";
import {
  type TypedMaterialization,
  materializeTypedBundle,
} from "./typed.js";

export interface OpenOkfSessionOptions extends LoadOptions {
  readonly schema?: string;
  readonly typedSchema?: string;
  readonly specTemplate?: string;
}

export class OkfDuckDbSession {
  readonly connection: DuckDBConnection;
  readonly bundle: Bundle;
  readonly normalized: ExportReport;
  readonly typed: TypedMaterialization;

  constructor(
    connection: DuckDBConnection,
    bundle: Bundle,
    normalized: ExportReport,
    typed: TypedMaterialization,
  ) {
    this.connection = connection;
    this.bundle = bundle;
    this.normalized = normalized;
    this.typed = typed;
  }

  close(): void {
    this.connection.closeSync();
  }
}

export async function openOkfSession(
  root: string | URL,
  options: OpenOkfSessionOptions = {},
): Promise<OkfDuckDbSession> {
  const bundle = await loadBundle(root, {
    ...(options.exclude === undefined ? {} : { exclude: options.exclude }),
    ...(options.readConcurrency === undefined ? {} : { readConcurrency: options.readConcurrency }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
    ...(options.rustCore === undefined ? {} : { rustCore: options.rustCore }),
  });
  const connection = await DuckDBConnection.create();
  try {
    const normalizedOptions: AttachOptions = {
      ...(options.schema === undefined ? {} : { schema: options.schema }),
    };
    const normalized = await attachBundle(connection, bundle, normalizedOptions);
    const conceptTypes = [...new Set(bundle.concepts.map((concept) => concept.conceptType))];
    const declarations = options.specTemplate === undefined
      ? {}
      : await loadDeclaredSchemas(bundle.root, conceptTypes, options.specTemplate);
    const typed = await materializeTypedBundle(connection, bundle, declarations, {
      ...(options.typedSchema === undefined ? {} : { schema: options.typedSchema }),
    });
    return new OkfDuckDbSession(connection, bundle, normalized, typed);
  } catch (error) {
    connection.closeSync();
    throw error;
  }
}
