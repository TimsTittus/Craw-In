import React from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  Briefcase,
  CheckCircle,
  Clock,
  ExternalLink,
  FileText,
  MapPin,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import {
  APPLICATION_TYPE_LABELS,
  EMPLOYMENT_LABELS,
  formatExperience,
  formatLocation,
  formatSalary,
  type JobPosting,
  type JobSearchOutcome,
  type JobSearchReport,
} from '@/lib/job-search';

const MAX_SKILLS_SHOWN = 8;
const MAX_NOTES_SHOWN = 5;

function scoreClass(score: number): string {
  if (score >= 80) return 'text-emerald-400 border-emerald-800/60 bg-emerald-950/30';
  if (score >= 60) return 'text-cyan-400 border-cyan-800/60 bg-cyan-950/30';
  return 'text-amber-400 border-amber-800/60 bg-amber-950/30';
}

function StatusBadge({ job }: { job: JobPosting }) {
  if (job.status === 'active' && job.application_verified) {
    return (
      <span className="flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-950/30 border border-emerald-800/50 px-2 py-0.5 rounded">
        <ShieldCheck className="w-3 h-3" /> Link verified
      </span>
    );
  }
  if (job.status === 'possibly_closed' || job.status === 'closed') {
    return (
      <span className="flex items-center gap-1 text-[10px] text-amber-400 bg-amber-950/30 border border-amber-800/50 px-2 py-0.5 rounded">
        <AlertTriangle className="w-3 h-3" /> May be closed
      </span>
    );
  }
  return <span className="text-[10px] text-slate-400 bg-slate-900 border border-slate-800 px-2 py-0.5 rounded">Not verified</span>;
}

function JobCard({ job, onViewDetails }: { job: JobPosting; onViewDetails: (job: JobPosting) => void }) {
  const experience = formatExperience(job);
  const salary = formatSalary(job);
  const meta = [
    job.employment_type ? EMPLOYMENT_LABELS[job.employment_type] : null,
    experience,
    salary,
  ].filter((item): item is string => Boolean(item));

  return (
    <div className="bg-slate-950/40 hover:bg-slate-950/90 border border-slate-800/80 hover:border-slate-700/60 rounded-xl p-5 transition-all duration-300 font-mono animate-in fade-in slide-in-from-bottom-2">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h3 className="text-sm font-semibold text-slate-100 break-words">{job.title}</h3>
          <p className="text-xs text-slate-300">{job.company ?? <span className="italic text-slate-500">Company not stated</span>}</p>
        </div>
        <span className={`shrink-0 text-xs font-bold px-2.5 py-1 rounded border ${scoreClass(job.match_score)}`} title="Match score">
          {job.match_score}%
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-[11px] text-slate-400">
        <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{formatLocation(job)}</span>
        <span className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {job.posted_label ? `Posted ${job.posted_label}` : 'Posted date unknown'}
        </span>
        {meta.map((item) => <span key={item}>{item}</span>)}
      </div>

      {job.skills.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {job.skills.slice(0, MAX_SKILLS_SHOWN).map((skill) => (
            <span key={skill} className="text-[10px] bg-slate-900 text-slate-300 px-2 py-0.5 rounded border border-slate-800/80">{skill}</span>
          ))}
        </div>
      )}

      {(job.match_reasons.length > 0 || job.concerns.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4 text-[11px]">
          {job.match_reasons.length > 0 && (
            <ul className="space-y-1">
              <li className="text-slate-500 uppercase tracking-wider text-[10px]">Why it matches</li>
              {job.match_reasons.slice(0, MAX_NOTES_SHOWN).map((reason) => (
                <li key={reason} className="flex items-start gap-1.5 text-emerald-300/90">
                  <CheckCircle className="w-3 h-3 mt-0.5 shrink-0" />{reason}
                </li>
              ))}
            </ul>
          )}
          {job.concerns.length > 0 && (
            <ul className="space-y-1">
              <li className="text-slate-500 uppercase tracking-wider text-[10px]">Potential concerns</li>
              {job.concerns.slice(0, MAX_NOTES_SHOWN).map((concern) => (
                <li key={concern} className="flex items-start gap-1.5 text-amber-300/90">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{concern}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 mt-4 pt-3 border-t border-slate-800/60">
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
          <span>via {job.sources.map((s) => s.name).join(', ') || job.source}</span>
          <StatusBadge job={job} />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onViewDetails(job)}
            className="flex items-center gap-1 px-3 py-1.5 text-[11px] text-slate-400 hover:text-white bg-slate-900 border border-slate-800 rounded-lg transition-colors"
          >
            <FileText className="w-3.5 h-3.5" /> Details
          </button>
          {job.source_url && (
            <a
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-3 py-1.5 text-[11px] text-slate-300 hover:text-white bg-slate-900 border border-slate-800 hover:border-slate-700 rounded-lg transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" /> View Job
            </a>
          )}
          {job.application_url ? (
            <a
              href={job.application_url}
              target="_blank"
              rel="noopener noreferrer"
              title={`Apply via ${APPLICATION_TYPE_LABELS[job.application_type]}`}
              className="flex items-center gap-1 px-3 py-1.5 text-[11px] font-bold text-slate-950 bg-emerald-600 hover:bg-emerald-500 border border-emerald-400/30 rounded-lg transition-colors"
            >
              Apply <ArrowUpRight className="w-3.5 h-3.5" />
            </a>
          ) : (
            <span className="px-3 py-1.5 text-[11px] text-slate-600 border border-slate-800 rounded-lg">No apply link</span>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryStrip({ report }: { report: JobSearchReport }) {
  const { summary } = report;
  const stats = [
    ['Discovered', summary.discovered],
    ['Unique', summary.unique],
    ['Crawled', summary.crawled],
    ['Qualified', summary.qualified],
    ['Verified links', summary.verified_applications],
  ] as const;
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
      {stats.map(([label, value]) => (
        <div key={label} className="bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
          <div className="text-lg font-bold text-white">{value}</div>
        </div>
      ))}
    </div>
  );
}

export function JobResults({
  outcome,
  onViewDetails,
}: {
  outcome: JobSearchOutcome;
  onViewDetails: (job: JobPosting) => void;
}) {
  if (outcome.status === 'idle') return null;

  if (outcome.status === 'loading') {
    return (
      <div className="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl p-6 font-mono text-xs text-slate-400">
        <RefreshCw className="w-4 h-4 text-cyan-400 animate-spin" />
        Searching job sources — live progress is streaming in the Agent Terminal.
      </div>
    );
  }

  if (outcome.status === 'error') {
    return (
      <div role="alert" className="flex items-start gap-3 bg-rose-950/20 border border-rose-900/40 rounded-xl p-5 font-mono text-xs text-rose-200">
        <AlertCircle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
        <div>
          <p className="font-bold mb-1">Job search failed</p>
          <p className="text-rose-300/80">{outcome.message}</p>
        </div>
      </div>
    );
  }

  const { report } = outcome;
  return (
    <div className="space-y-4 font-mono">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
        <Briefcase className="h-4 w-4 text-emerald-400" />
        Job Matches
        <span className="text-[10px] font-normal text-slate-500">
          {report.jobs.length} ranked · {report.summary.duration_seconds.toFixed(1)}s{report.parser ? ` · ${report.parser}` : ''}
        </span>
      </h3>

      <SummaryStrip report={report} />

      {(report.warnings.length > 0 || report.droppedJobs > 0) && (
        <div className="bg-amber-950/20 border border-amber-900/40 rounded-lg p-3 text-[11px] text-amber-200 space-y-1">
          {report.warnings.map((warning) => (
            <p key={warning} className="flex items-start gap-1.5"><AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{warning}</p>
          ))}
          {report.droppedJobs > 0 && (
            <p className="flex items-start gap-1.5">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{report.droppedJobs} malformed job records were skipped.
            </p>
          )}
        </div>
      )}

      {report.jobs.length === 0 ? (
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 text-center text-xs text-slate-400">
          No jobs matched your requirements. Try broader roles, more locations, or a longer posting window.
        </div>
      ) : (
        report.jobs.map((job) => <JobCard key={job.job_id} job={job} onViewDetails={onViewDetails} />)
      )}
    </div>
  );
}
