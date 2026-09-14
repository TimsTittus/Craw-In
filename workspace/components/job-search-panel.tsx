import React, { useState } from 'react';
import { Briefcase, Search, SlidersHorizontal } from 'lucide-react';
import type { EmploymentType, JobSearchFilters, RemotePreference } from '@/lib/job-search';

const inputClass =
  'w-full bg-slate-900 border border-slate-800 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 text-slate-200 placeholder-slate-600';

function toNumber(value: string): number | undefined {
  const parsed = Number(value);
  return value.trim() !== '' && Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

export function JobSearchPanel({
  onSearch,
  disabled,
}: {
  onSearch: (prompt: string, filters: JobSearchFilters) => void;
  disabled: boolean;
}) {
  const [prompt, setPrompt] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [role, setRole] = useState('');
  const [locations, setLocations] = useState('');
  const [remote, setRemote] = useState<RemotePreference | ''>('');
  const [skills, setSkills] = useState('');
  const [employmentType, setEmploymentType] = useState<EmploymentType | ''>('');
  const [minYears, setMinYears] = useState('');
  const [maxYears, setMaxYears] = useState('');
  const [postedWithin, setPostedWithin] = useState('');
  const [salaryMin, setSalaryMin] = useState('');

  const canSubmit = !disabled && (prompt.trim() !== '' || role.trim() !== '');

  const submit = () => {
    if (!canSubmit) return;
    const filters: JobSearchFilters = {
      role: role.trim() || undefined,
      locations: locations.trim() || undefined,
      remote: remote || undefined,
      skills: skills.trim() || undefined,
      employment_type: employmentType || undefined,
      min_experience_years: toNumber(minYears),
      max_experience_years: toNumber(maxYears),
      posted_within_days: toNumber(postedWithin),
      salary_min: toNumber(salaryMin),
    };
    onSearch(prompt.trim(), filters);
  };

  return (
    <div className="w-full bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 border border-slate-800 rounded-xl p-6 shadow-2xl font-mono">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-emerald-500/10 rounded-lg border border-emerald-500/20">
            <Briefcase className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-md font-semibold text-slate-200 tracking-wide">Job Search</h2>
            <p className="text-xs text-slate-400">Describe the roles you want — results are ranked with direct apply links.</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowFilters((v) => !v)}
          aria-expanded={showFilters}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded border text-xs transition-colors ${showFilters
            ? 'bg-emerald-500/10 border-emerald-500 text-emerald-400'
            : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700'
            }`}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span>FILTERS</span>
        </button>
      </div>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        aria-label="Job requirements"
        placeholder="e.g. Cybersecurity internships in Bangalore or remote India, 0–1 years experience, SOC / SIEM / Python / Linux, posted in the last 14 days"
        className="w-full h-24 bg-slate-950/80 border border-slate-800 focus:border-emerald-500/50 rounded-lg p-4 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-emerald-500/30 transition-all duration-300 resize-none"
      />

      {showFilters && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4 text-xs">
          <div>
            <label htmlFor="job-role" className="block text-slate-400 mb-1">ROLE</label>
            <input id="job-role" value={role} onChange={(e) => setRole(e.target.value)} placeholder="Security Analyst" className={inputClass} />
          </div>
          <div>
            <label htmlFor="job-locations" className="block text-slate-400 mb-1">LOCATIONS</label>
            <input id="job-locations" value={locations} onChange={(e) => setLocations(e.target.value)} placeholder="Bangalore, Pune" className={inputClass} />
          </div>
          <div>
            <label htmlFor="job-remote" className="block text-slate-400 mb-1">REMOTE</label>
            <select id="job-remote" value={remote} onChange={(e) => setRemote(e.target.value as RemotePreference | '')} className={inputClass}>
              <option value="">From description</option>
              <option value="any">Any</option>
              <option value="remote_ok">Remote OK</option>
              <option value="remote_only">Remote only</option>
              <option value="onsite_only">On-site only</option>
            </select>
          </div>
          <div>
            <label htmlFor="job-skills" className="block text-slate-400 mb-1">SKILLS</label>
            <input id="job-skills" value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="Python, SIEM, Linux" className={inputClass} />
          </div>
          <div>
            <label htmlFor="job-employment" className="block text-slate-400 mb-1">EMPLOYMENT TYPE</label>
            <select id="job-employment" value={employmentType} onChange={(e) => setEmploymentType(e.target.value as EmploymentType | '')} className={inputClass}>
              <option value="">From description</option>
              <option value="full_time">Full-time</option>
              <option value="part_time">Part-time</option>
              <option value="contract">Contract</option>
              <option value="internship">Internship</option>
              <option value="temporary">Temporary</option>
            </select>
          </div>
          <div>
            <label htmlFor="job-posted" className="block text-slate-400 mb-1">POSTED WITHIN</label>
            <select id="job-posted" value={postedWithin} onChange={(e) => setPostedWithin(e.target.value)} className={inputClass}>
              <option value="">From description</option>
              <option value="1">Today</option>
              <option value="3">3 days</option>
              <option value="7">7 days</option>
              <option value="14">14 days</option>
              <option value="30">30 days</option>
            </select>
          </div>
          <div>
            <span className="block text-slate-400 mb-1">EXPERIENCE (YEARS)</span>
            <div className="flex items-center gap-2">
              <input aria-label="Minimum years of experience" type="number" min={0} max={50} value={minYears} onChange={(e) => setMinYears(e.target.value)} placeholder="min" className={inputClass} />
              <span className="text-slate-600">–</span>
              <input aria-label="Maximum years of experience" type="number" min={0} max={50} value={maxYears} onChange={(e) => setMaxYears(e.target.value)} placeholder="max" className={inputClass} />
            </div>
          </div>
          <div>
            <label htmlFor="job-salary" className="block text-slate-400 mb-1">MIN SALARY</label>
            <input id="job-salary" type="number" min={0} value={salaryMin} onChange={(e) => setSalaryMin(e.target.value)} placeholder="optional" className={inputClass} />
          </div>
        </div>
      )}

      <div className="flex justify-end mt-4">
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 font-bold text-xs rounded border border-emerald-400/30 transition-all duration-200 flex items-center space-x-1.5 shadow-lg shadow-emerald-900/20"
        >
          <Search className="w-3.5 h-3.5" />
          <span>FIND JOBS</span>
        </button>
      </div>
    </div>
  );
}
