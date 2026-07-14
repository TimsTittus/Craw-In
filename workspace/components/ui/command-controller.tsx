import React, { useState } from 'react';
import { Terminal, Shield, Cpu, Layers } from 'lucide-react';

export function CommandControllerPanel({ onDispatch }: { onDispatch: (query: string) => void }) {
  const [query, setQuery] = useState('');

  return (
    <div className="w-full bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 border border-slate-800 rounded-xl p-6 shadow-2xl">
      <div className="flex items-center space-x-3 mb-4">
        <div className="p-2 bg-emerald-500/10 rounded-lg border border-emerald-500/20">
          <Terminal className="w-5 h-5 text-emerald-400" />
        </div>
        <div>
          <h2 className="text-md font-semibold text-slate-200 font-mono tracking-wide">Information Operating System</h2>
          <p className="text-xs text-slate-400 font-mono">Status: Core Engine Online</p>
        </div>
      </div>

      <div className="relative group">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Enter objective directive (e.g., 'Analyze threat vector patterns for LLM agent deployments')..."
          className="w-full h-32 bg-slate-950/80 border border-slate-800 focus:border-emerald-500/50 rounded-lg p-4 font-mono text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-emerald-500/30 transition-all duration-300 resize-none"
        />
        <div className="absolute bottom-3 right-3 flex items-center space-x-2">
          <button
            onClick={() => onDispatch(query)}
            disabled={!query.trim()}
            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 font-mono font-bold text-xs rounded border border-emerald-400/30 transition-all duration-200 flex items-center space-x-1.5 shadow-lg shadow-emerald-900/20"
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>EXECUTE OBJECTIVE</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 mt-4 pt-4 border-t border-slate-800/60 text-[11px] font-mono text-slate-400">
        <div className="flex items-center space-x-2"><Shield className="w-3.5 h-3.5 text-cyan-400" /> <span>Encrypted Sandbox</span></div>
        <div className="flex items-center space-x-2"><Layers className="w-3.5 h-3.5 text-purple-400" /> <span>Dual-Vector Fabric</span></div>
        <div className="flex items-center space-x-2"><div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" /> <span>Local Models Bound</span></div>
      </div>
    </div>
  );
}