import { createHash } from "node:crypto";

import type { FrontmatterObject, FrontmatterValue } from "./core.js";

function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function normalizeNewlines(text: string): string {
  return text.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
}

function assertJcsString(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) {
        throw new TypeError("JCS strings must not contain lone UTF-16 surrogates");
      }
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new TypeError("JCS strings must not contain lone UTF-16 surrogates");
    }
  }
}

function jcsString(value: string): string {
  assertJcsString(value);
  return JSON.stringify(value);
}

export function canonicalJson(value: FrontmatterValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return jcsString(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as FrontmatterObject;
  const properties = Object.keys(object).sort();
  return `{${properties
    .map((property) => `${jcsString(property)}:${canonicalJson(object[property] ?? null)}`)
    .join(",")}}`;
}

export function sourceDigest(content: string): string {
  return `sha256:${sha256(content)}`;
}

export function parsedDigest(
  frontmatter: Readonly<Record<string, FrontmatterValue>>,
  body: string,
): string {
  const payload: FrontmatterValue = [frontmatter, normalizeNewlines(body)];
  return `okf-parsed-v1-jcs-sha256:${sha256(canonicalJson(payload))}`;
}
