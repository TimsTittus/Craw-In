import type { AnalyzedResult } from "@/components/ui/analytics-display";

// Types mirror backend/plugins/job_search_plugin/schema.py (JobSearchReport).

export type JobStatus = "active" | "possibly_closed" | "closed" | "unknown";
export type ApplicationType = "direct_company" | "ats" | "job_board" | "external" | "unknown";
export type WorkMode = "remote" | "hybrid" | "onsite";
export type RemotePreference = "any" | "remote_ok" | "remote_only" | "onsite_only";
export type EmploymentType = "full_time" | "part_time" | "contract" | "internship" | "temporary";

export interface JobSourceRef {
  name: string;
  url: string;
  kind: string;
}

export interface JobPosting {
  job_id: string;
  title: string;
  company: string | null;
  locations: string[];
  remote: WorkMode | null;
  employment_type: EmploymentType | null;
  experience_level: string | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  skills: string[];
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  description: string;
  requirements: string[];
  preferred_qualifications: string[];
  posted_at: string | null;
  posted_at_estimated: boolean;
  /** Human-readable age ("3 days ago"), computed once when the result arrives. */
  posted_label: string | null;
  source: string;
  source_url: string | null;
  sources: JobSourceRef[];
  application_url: string | null;
  application_type: ApplicationType;
  application_verified: boolean;
  last_verified_at: string | null;
  status: JobStatus;
  match_score: number;
  match_reasons: string[];
  concerns: string[];
}

export interface JobSearchSummary {
  queries_generated: number;
  searches_failed: number;
  discovered: number;
  unique: number;
  crawled: number;
  qualified: number;
  returned: number;
  verified_applications: number;
  duration_seconds: number;
}

export interface JobSearchReport {
  summary: JobSearchSummary;
  jobs: JobPosting[];
  warnings: string[];
  error: string | null;
  parser: string | null;
  /** Jobs dropped on the client because they failed validation. */
  droppedJobs: number;
}

export interface JobSearchFilters {
  role?: string;
  locations?: string;
  remote?: RemotePreference;
  skills?: string;
  employment_type?: EmploymentType;
  min_experience_years?: number;
  max_experience_years?: number;
  posted_within_days?: number;
  salary_min?: number;
}

export type JobSearchOutcome =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; report: JobSearchReport }
  | { status: "error"; message: string };

export const EMPLOYMENT_LABELS: Record<EmploymentType, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  internship: "Internship",
  temporary: "Temporary",
};

export const APPLICATION_TYPE_LABELS: Record<ApplicationType, string> = {
  direct_company: "Company site",
  ats: "Company ATS",
  job_board: "Job board",
  external: "External site",
  unknown: "Unknown",
};

const JOB_STATUSES: readonly JobStatus[] = ["active", "possibly_closed", "closed", "unknown"];
const APPLICATION_TYPES: readonly ApplicationType[] = ["direct_company", "ats", "job_board", "external", "unknown"];
const WORK_MODES: readonly WorkMode[] = ["remote", "hybrid", "onsite"];
const EMPLOYMENT_TYPES = Object.keys(EMPLOYMENT_LABELS) as EmploymentType[];
const MAX_LIST_ITEMS = 30;
const DAY_MS = 86_400_000;

// ── Runtime validation: the payload crosses a network boundary and wraps untrusted page data ──

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string").slice(0, MAX_LIST_ITEMS) : [];
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

/** Only absolute http(s) URLs are ever rendered as links (blocks javascript:, data:, etc.). */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function postedLabel(postedAt: string | null, estimated: boolean, now: number): string | null {
  if (!postedAt) return null;
  const time = Date.parse(postedAt);
  if (Number.isNaN(time)) return null;
  const days = Math.max(0, Math.floor((now - time) / DAY_MS));
  const label = days === 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
  return estimated ? `~${label}` : label;
}

function parseJob(value: unknown, now: number): JobPosting | null {
  if (!isRecord(value)) return null;
  const title = str(value.title);
  const jobId = str(value.job_id);
  if (!title || !jobId) return null;
  const postedAt = str(value.posted_at);
  const estimated = value.posted_at_estimated === true;
  const sources = Array.isArray(value.sources)
    ? value.sources.flatMap((s) => {
        if (!isRecord(s)) return [];
        const url = safeHttpUrl(s.url);
        const name = str(s.name);
        return url && name ? [{ name, url, kind: str(s.kind) ?? "web" }] : [];
      })
    : [];
  return {
    job_id: jobId,
    title,
    company: str(value.company),
    locations: strList(value.locations),
    remote: oneOf(value.remote, WORK_MODES),
    employment_type: oneOf(value.employment_type, EMPLOYMENT_TYPES),
    experience_level: str(value.experience_level),
    experience_min_years: num(value.experience_min_years),
    experience_max_years: num(value.experience_max_years),
    skills: strList(value.skills),
    salary_min: num(value.salary_min),
    salary_max: num(value.salary_max),
    salary_currency: str(value.salary_currency),
    salary_period: str(value.salary_period),
    description: str(value.description) ?? "",
    requirements: strList(value.requirements),
    preferred_qualifications: strList(value.preferred_qualifications),
    posted_at: postedAt,
    posted_at_estimated: estimated,
    posted_label: postedLabel(postedAt, estimated, now),
    source: str(value.source) ?? "Unknown source",
    source_url: safeHttpUrl(value.source_url),
    sources,
    application_url: safeHttpUrl(value.application_url),
    application_type: oneOf(value.application_type, APPLICATION_TYPES) ?? "unknown",
    application_verified: value.application_verified === true,
    last_verified_at: str(value.last_verified_at),
    status: oneOf(value.status, JOB_STATUSES) ?? "unknown",
    match_score: Math.min(100, Math.max(0, Math.round(num(value.match_score) ?? 0))),
    match_reasons: strList(value.match_reasons),
    concerns: strList(value.concerns),
  };
}

/** Validate a backend job search report. Returns null when the payload is unusable. */
export function parseJobSearchReport(value: unknown, now: number = Date.now()): JobSearchReport | null {
  if (!isRecord(value)) return null;
  const error = str(value.error);
  if (!error && !Array.isArray(value.jobs)) return null;
  const rawJobs: unknown[] = Array.isArray(value.jobs) ? value.jobs : [];
  const jobs = rawJobs.map((job) => parseJob(job, now)).filter((job): job is JobPosting => job !== null);
  const summary = isRecord(value.summary) ? value.summary : {};
  const count = (key: string) => num(summary[key]) ?? 0;
  const query = isRecord(value.query) ? value.query : {};
  return {
    summary: {
      queries_generated: count("queries_generated"),
      searches_failed: count("searches_failed"),
      discovered: count("discovered"),
      unique: count("unique"),
      crawled: count("crawled"),
      qualified: count("qualified"),
      returned: count("returned"),
      verified_applications: count("verified_applications"),
      duration_seconds: count("duration_seconds"),
    },
    jobs,
    warnings: strList(value.warnings),
    error,
    parser: str(query.parser),
    droppedJobs: rawJobs.length - jobs.length,
  };
}

/** Map a raw `job_search` WebSocket payload to a UI outcome. */
export function toJobOutcome(payload: unknown): JobSearchOutcome {
  const report = parseJobSearchReport(payload);
  if (!report) return { status: "error", message: "Received a malformed job search result." };
  if (report.error) return { status: "error", message: report.error };
  return { status: "done", report };
}

// ── Formatting ──

export function formatLocation(job: JobPosting): string {
  const place = job.locations.join(" / ");
  const mode = job.remote ? job.remote.charAt(0).toUpperCase() + job.remote.slice(1) : null;
  if (place && mode) return `${place} · ${mode}`;
  return place || mode || "Location not stated";
}

export function formatExperience(job: JobPosting): string | null {
  const { experience_min_years: low, experience_max_years: high } = job;
  if (low === null) return job.experience_level ? `${job.experience_level} level` : null;
  if (high === null) return `${low}+ yrs`;
  return low === high ? `${low} yrs` : `${low}–${high} yrs`;
}

export function formatSalary(job: JobPosting): string | null {
  const { salary_min: low, salary_max: high, salary_currency: currency, salary_period: period } = job;
  if (low === null && high === null) return null;
  const range = low !== null && high !== null && low !== high
    ? `${low.toLocaleString()}–${high.toLocaleString()}`
    : (low ?? high)!.toLocaleString();
  return [currency, range, period ? `/ ${period}` : null].filter(Boolean).join(" ");
}

/** Drop Markdown links whose target is not an absolute http(s) URL (keeps their text). */
function stripUnsafeLinks(markdown: string): string {
  return markdown.replace(/\[([^\]]*)\]\(([^)]*)\)/g, (match, text: string, url: string) =>
    safeHttpUrl(url.trim()) ? match : text,
  );
}

function jobToMarkdown(job: JobPosting): string {
  const lines = [
    `# ${job.title}`,
    `- **Company:** ${job.company ?? "Not stated"}`,
    `- **Location:** ${formatLocation(job)}`,
    `- **Employment:** ${job.employment_type ? EMPLOYMENT_LABELS[job.employment_type] : "Not stated"}`,
    `- **Experience:** ${formatExperience(job) ?? "Not stated"}`,
    `- **Salary:** ${formatSalary(job) ?? "Not stated"}`,
    `- **Posted:** ${job.posted_label ?? "Unknown"}`,
    `- **Application:** ${APPLICATION_TYPE_LABELS[job.application_type]} (${job.status.replace("_", " ")})`,
  ];
  if (job.application_url) lines.push(`- **Apply:** [${job.application_url}](${job.application_url})`);
  if (job.requirements.length) lines.push("", "## Requirements", ...job.requirements.map((r) => `- ${r}`));
  if (job.preferred_qualifications.length) {
    lines.push("", "## Preferred Qualifications", ...job.preferred_qualifications.map((r) => `- ${r}`));
  }
  if (job.description) lines.push("", "---", "", "## Description", stripUnsafeLinks(job.description));
  if (job.sources.length) lines.push("", "## Sources", ...job.sources.map((s) => `- [${s.name}](${s.url})`));
  return lines.join("\n");
}

/** Adapt a job to the dashboard's existing side-panel document shape. */
export function jobToAnalyzedResult(job: JobPosting): AnalyzedResult {
  return {
    id: job.job_id,
    title: job.company ? `${job.title} — ${job.company}` : job.title,
    source: job.sources.map((s) => s.name).join(", ") || job.source,
    confidence: job.match_score / 100,
    indicators: job.match_reasons,
    content: jobToMarkdown(job),
  };
}
