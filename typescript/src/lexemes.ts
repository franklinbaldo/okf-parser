export type LexemeKind = "string" | "boolean" | "integer" | "number" | "date" | "datetime";

const INTEGER_RE = /^[+-]?[0-9]+$/u;
const NUMBER_RE = /^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$/u;
const DATE_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/u;
const DATETIME_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?(?:[Zz]|[+-]([0-9]{2}):([0-9]{2}))?$/u;

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function isCalendarDate(year: number, month: number, day: number): boolean {
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  const days = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= (days[month - 1] ?? 0);
}

export function isIsoDateLexeme(value: string): boolean {
  const match = DATE_RE.exec(value);
  return (
    match !== null &&
    isCalendarDate(Number(match[1]), Number(match[2]), Number(match[3]))
  );
}

export function isIsoDateTimeLexeme(value: string): boolean {
  const match = DATETIME_RE.exec(value);
  if (match === null) return false;
  if (!isCalendarDate(Number(match[1]), Number(match[2]), Number(match[3]))) return false;
  return (
    Number(match[4]) <= 23 &&
    Number(match[5]) <= 59 &&
    Number(match[6] ?? "0") <= 59 &&
    Number(match[8] ?? "0") <= 23 &&
    Number(match[9] ?? "0") <= 59
  );
}

export function classifyLexemes(values: readonly string[]): LexemeKind {
  if (values.length === 0) return "string";
  if (values.every((value) => /^(?:true|false)$/iu.test(value))) return "boolean";
  if (values.every((value) => INTEGER_RE.test(value))) return "integer";
  if (values.every((value) => NUMBER_RE.test(value))) return "number";
  if (values.every(isIsoDateLexeme)) return "date";
  if (values.every(isIsoDateTimeLexeme)) return "datetime";
  return "string";
}

export function canClassifyAs(values: readonly string[], kind: LexemeKind): boolean {
  if (values.length === 0 || kind === "string") return true;
  const inferred = classifyLexemes(values);
  if (kind === "number") return inferred === "integer" || inferred === "number";
  if (kind === "datetime") return inferred === "date" || inferred === "datetime";
  return inferred === kind;
}
