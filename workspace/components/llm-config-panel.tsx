import React, { useState, useEffect } from 'react';
import { Cpu, Globe, Key, Settings } from 'lucide-react';

export interface LLMConfigState {
  provider: 'local_ollama' | 'cloud_openai' | 'cloud_deepseek';
  model: string;
  api_key: string;
  base_url: string;
}

export function LLMConfigPanel({ onConfigChange }: { onConfigChange: (config: LLMConfigState) => void }) {
  const [provider, setProvider] = useState<LLMConfigState['provider']>('local_ollama');
  const [model, setModel] = useState('auto');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('http://localhost:11434');

  useEffect(() => {
    onConfigChange({ provider, model, api_key: apiKey, base_url: baseUrl });
  }, [provider, model, apiKey, baseUrl]);

  return (
    <div className="w-full bg-slate-950 border border-slate-800 rounded-xl p-5 font-mono shadow-xl text-slate-200">
      <div className="flex items-center space-x-2 mb-4 border-b border-slate-800 pb-3">
        <Settings className="w-4 h-4 text-emerald-400 animate-spin-slow" />
        <h3 className="text-sm font-bold tracking-wider text-slate-300">CORE INFRASTRUCTURE ROUTING</h3>
      </div>

      <div className="space-y-4 text-xs">
        <div>
          <label className="block text-slate-400 mb-2">TARGET PROVISIONER LAYER</label>
          <div className="grid grid-cols-3 gap-2">
            {[
              { id: 'local_ollama', label: 'OLLAMA (LOCAL)', icon: Cpu },
              { id: 'cloud_openai', label: 'OPENAI (CLOUD)', icon: Globe },
              { id: 'cloud_deepseek', label: 'DEEPSEEK', icon: Globe },
            ].map((p) => {
              const Icon = p.icon;
              return (
                <button
                  key={p.id}
                  onClick={() => {
                    setProvider(p.id as LLMConfigState['provider']);
                    if (p.id === 'local_ollama') {
                      setBaseUrl('http://localhost:11434');
                      setModel('auto');
                    } else if (p.id === 'cloud_openai') {
                      setBaseUrl('https://api.openai.com/v1');
                      setModel('gpt-4o');
                    } else {
                      setBaseUrl('https://api.deepseek.com/v1');
                      setModel('deepseek-chat');
                    }
                  }}
                  className={`flex items-center justify-center space-x-2 p-2 rounded border transition-all duration-200 ${provider === p.id
                    ? 'bg-emerald-500/10 border-emerald-500 text-emerald-400'
                    : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700'
                    }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span>{p.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-slate-400 mb-1">TARGET IDENTITY MODEL</label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 text-slate-200"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1">BASE API HOST GATEWAY</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 text-slate-200"
            />
          </div>
        </div>

        {provider !== 'local_ollama' && (
          <div className="pt-2 animate-fade-in">
            <label className="block text-slate-400 mb-1 flex items-center space-x-1">
              <Key className="w-3 h-3 text-amber-400" />
              <span>CRYPTOGRAPHIC PROVIDER TOKEN (API KEY)</span>
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-........................................"
              className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-1.5 focus:outline-none focus:border-emerald-500 text-slate-200 tracking-widest"
            />
          </div>
        )}
      </div>
    </div>
  );
}
