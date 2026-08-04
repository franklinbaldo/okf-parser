import { stat } from "node:fs/promises";
import path from "node:path";

import type { Diagnostic, Severity } from "./core.js";
import { OkfParserError } from "./core.js";

export const SPEC_SLUG_PLACEHOLDER = "{slug}";

const SEPARATOR_RE = /[\s/]+/gu;
const REMOVED_RE = /[^a-z0-9-]+/gu;
const HYPHEN_RUN_RE = /-{2,}/gu;
const COMBINING_RE = /\p{M}/gu;

/**
 * Derive the filesystem-safe slug of one producer-defined type. Accents and
 * cedillas are removed, whitespace and `/` become hyphens, and every remaining
 * non-alphanumeric character is dropped.
 */
export function typeSlug(conceptType: string): string {
  const stripped = conceptType.normalize("NFKD").replace(COMBINING_RE, "");
  const hyphenated = stripped.trim().toLowerCase().replace(SEPARATOR_RE, "-");
  return hyphenated.replace(REMOVED_RE, "").replace(HYPHEN_RUN_RE, "-").replace(/^-+|-+$/gu, "");
}

/**
 * Render the bundle-relative document path expected for one type, or `null`
 * when the type has no slug at all. A type without a slug is a bundle fact and
 * becomes a diagnostic; only an unusable template is an operator error.
 */
export function specRelativePath(template: string, conceptType: string): string | null {
  if (!template.includes(SPEC_SLUG_PLACEHOLDER)) {
    throw new OkfParserError(
      "OKF_SPEC_TEMPLATE",
      `specification template must contain ${SPEC_SLUG_PLACEHOLDER}: ${JSON.stringify(template)}`,
    );
  }
  const slug = typeSlug(conceptType);
  return slug === "" ? null : template.split(SPEC_SLUG_PLACEHOLDER).join(slug);
}

async function isFile(target: string): Promise<boolean> {
  try {
    return (await stat(target)).isFile();
  } catch {
    return false;
  }
}

/**
 * Report every type in use whose derived specification document is absent. The
 * diagnostic is advisory unless `normative` is set: a bundle mid-adoption
 * legitimately has legacy types without a document.
 */
export async function missingTypeSpecs(
  root: string,
  conceptTypes: Iterable<string>,
  template: string,
  options: { readonly normative?: boolean } = {},
): Promise<readonly Diagnostic[]> {
  const severity: Severity = options.normative === true ? "error" : "warning";
  const unique = [...new Set(conceptTypes)].filter((item) => item !== "").sort();
  const diagnostics: Diagnostic[] = [];
  for (const conceptType of unique) {
    const relative = specRelativePath(template, conceptType);
    if (relative === null) {
      diagnostics.push(
        Object.freeze({
          code: "OKF010",
          severity,
          path: template,
          message: `type ${JSON.stringify(conceptType)} has no slug usable as a document path`,
        }),
      );
      continue;
    }
    if (await isFile(path.resolve(root, relative))) {
      continue;
    }
    diagnostics.push(
      Object.freeze({
        code: "OKF010",
        severity,
        path: relative,
        message: `type ${JSON.stringify(conceptType)} has no specification document`,
      }),
    );
  }
  return Object.freeze(diagnostics);
}
