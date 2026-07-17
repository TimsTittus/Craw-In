import React from 'react';
import { ExternalLink, ShieldCheck, ArrowUpRight } from 'lucide-react';

export interface AnalyzedResult {
  id: string;
  title: string;
  source: string;
  confidence: number;
  indicators: string[];
  content?: string;
}

export function AnalyticsDisplayGrid({
  results,
  onViewDetails
}: {
  results: AnalyzedResult[];
  onViewDetails?: (result: AnalyzedResult) => void;
}) {
  if (!results || results.length === 0) return null;
  return (
    <div className="space-y-4">
      {results.map((result) => (
        <div
          key={result.id}
          className="group relative bg-slate-950/40 hover:bg-slate-950/90 border border-slate-800/80 hover:border-slate-700/60 rounded-xl p-5 transition-all duration-300 flex items-start justify-between backdrop-blur-sm animate-in fade-in slide-in-from-bottom-2 duration-300"
        >
          <div className="space-y-3 flex-1">
            <div className="flex items-center space-x-2.5">
              <span className="text-xs bg-slate-800 text-slate-300 font-mono px-2 py-0.5 rounded border border-slate-700/50">
                {result.source}
              </span>
              <div className="flex items-center space-x-1 text-emerald-400 text-xs font-mono">
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>{(result.confidence * 100).toFixed(0)}% Match</span>
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-slate-100 group-hover:text-emerald-400 transition-colors duration-200 font-mono">
                {result.title}
              </h3>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {result.indicators.map((indicator, idx) => (
                <span key={idx} className="text-[10px] bg-slate-900 text-slate-400 font-mono px-2 py-0.5 rounded border border-slate-800/80">
                  {indicator}
                </span>
              ))}
            </div>
          </div>

          <div className="ml-4">
            <button
              onClick={() => onViewDetails?.(result)}
              className="p-2 bg-slate-900 hover:bg-emerald-950/40 hover:text-emerald-400 text-slate-400 rounded-lg border border-slate-800 hover:border-emerald-800/60 transition-all duration-200"
              title="View Full Details"
            >
              <ArrowUpRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}