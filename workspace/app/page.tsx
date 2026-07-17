"use client";

import React, { useState, useEffect, useRef } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Terminal,
  Activity,
  Database,
  Search,
  Send,
  Globe,
  RefreshCw,
  AlertCircle,
  CheckCircle,
  Network,
  Cpu
} from "lucide-react";
import { CommandControllerPanel } from "@/components/ui/command-controller";
import { AnalyticsDisplayGrid, AnalyzedResult } from "@/components/ui/analytics-display";
import { LLMConfigPanel, LLMConfigState } from "@/components/llm-config-panel";

interface LogStep {
  step: string;
  status: "idle" | "running" | "completed" | "failed";
  details?: string;
  timestamp: string;
}

export default function WorkspaceDashboard() {
  const [taskId, setTaskId] = useState("session-interactive");
  const [prompt, setPrompt] = useState("");
  const [logs, setLogs] = useState<LogStep[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [analyzedResults, setAnalyzedResults] = useState<AnalyzedResult[]>([]);
  const [connected, setConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [llmConfig, setLlmConfig] = useState<LLMConfigState | null>(null);
  const [pingStatus, setPingStatus] = useState<{ status: 'idle' | 'testing' | 'success' | 'error', message: string }>({ status: 'idle', message: '' });
  const socketRef = useRef<WebSocket | null>(null);
  const logsEndRef = useRef<HTMLDivElement | null>(null);

  // Initialize WebSocket connection
  const connectWebSocket = () => {
    if (socketRef.current) {
      socketRef.current.close();
    }

    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/tasks";
    const fullUrl = `${wsUrl}/${taskId}`;

    console.log(`Connecting to WebSocket: ${fullUrl}`);
    const ws = new WebSocket(fullUrl);
    socketRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      addLocalLog("System", "completed", `Connected to agent pipeline telemetry at ${fullUrl}`);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "telemetry" && data.steps) {
          setLogs(data.steps);
          if (data.status === "completed" || data.status === "failed") {
            setIsRunning(false);
          }
        } else if (data.type === "result") {
          setResult(data.content);
          if (data.results_json) {
            setAnalyzedResults(data.results_json);
          }
        } else {
          // Fallback if raw text or other payload format is sent
          addLocalLog(data.step || "Update", data.status || "completed", data.details || event.data);
        }
      } catch (err) {
        addLocalLog("Raw Stream", "completed", event.data);
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setIsRunning(false);
      addLocalLog("System", "failed", "WebSocket connection closed.");
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      addLocalLog("System", "failed", "WebSocket encountered an error.");
    };
  };

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [taskId]);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  const addLocalLog = (step: string, status: LogStep["status"], details: string) => {
    setLogs((prev) => [
      ...prev,
      {
        step,
        status,
        details,
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
  };

  const triggerPlanner = (queryText: string = prompt) => {
    if (!queryText.trim()) return;
    if (!connected || !socketRef.current) {
      addLocalLog("System", "failed", "Cannot run plan: WebSocket not connected.");
      return;
    }

    setIsRunning(true);
    setLogs([]); // Reset logs for the new run
    setResult(null);
    setAnalyzedResults([]);

    // Send command payload over the WebSocket link
    const payload = {
      action: "run_agent",
      prompt: queryText,
      llm_config: llmConfig || { provider: "local_ollama", model: "qwen2.5:7b" }
    };

    socketRef.current.send(JSON.stringify(payload));
    addLocalLog("Dispatcher", "running", `Dispatched planning task: "${queryText}" using ${payload.llm_config.provider} (${payload.llm_config.model})`);
  };

  const pingBackend = async () => {
    if (!llmConfig) return;
    setPingStatus({ status: 'testing', message: 'Pinging backend...' });
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${apiUrl}/api/v1/dispatch`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: 'ping',
          llm_config: llmConfig
        })
      });
      if (res.ok) {
        const data = await res.json();
        setPingStatus({ status: 'success', message: `Success! Active: ${data.config_active}` });
      } else {
        let error = await res.text();
        try { error = JSON.parse(error).detail || error; } catch (e) { }
        setPingStatus({ status: 'error', message: `Error: ${res.statusText} - ${error}` });
      }
    } catch (e: any) {
      setPingStatus({ status: 'error', message: `Network Error: ${e.message}` });
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans selection:bg-cyan-500/30">
      <div className="max-w-7xl mx-auto space-y-8">

        {/* Header */}
        <header className="flex flex-col md:flex-row md:items-center md:justify-between border-b border-slate-800 pb-6 gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
              <span className="text-xs font-semibold uppercase tracking-wider text-cyan-400">Telemetry Active</span>
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
              Personal AI Information OS
            </h1>
            <p className="text-slate-400 text-sm">
              Control workspace indexers, crawler nodes, and local reasoning loops.
            </p>
          </div>

          {/* Connection Status Bar */}
          <div className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-xl px-4 py-2 text-xs">
            <div className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${connected ? "bg-emerald-500 animate-ping" : "bg-rose-500"}`} />
              <span className="font-medium text-slate-300">
                {connected ? "Link Established" : "Disconnected"}
              </span>
            </div>
            <span className="text-slate-600">|</span>
            <div className="flex items-center gap-1.5">
              <span className="text-slate-400 font-mono">ID:</span>
              <input
                type="text"
                value={taskId}
                onChange={(e) => setTaskId(e.target.value)}
                className="bg-transparent border-none text-cyan-400 font-mono outline-none w-32 focus:ring-0"
              />
            </div>
            <button
              onClick={connectWebSocket}
              className="text-slate-400 hover:text-white transition-colors"
              title="Reconnect"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </header>

        {/* Dashboard Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

          {/* Left / Center Grid (Controls & Status Panels) */}
          <div className="lg:col-span-2 space-y-8">

            {/* Core Infrastructure Routing */}
            <div className="space-y-4">
              <LLMConfigPanel onConfigChange={setLlmConfig} />

              <div className="flex items-center space-x-4">
                <Button
                  onClick={pingBackend}
                  disabled={pingStatus.status === 'testing' || !llmConfig}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white border-none"
                >
                  {pingStatus.status === 'testing' ? 'Pinging...' : 'Ping Active Configuration'}
                </Button>

                {pingStatus.status !== 'idle' && (
                  <span className={`text-xs font-mono px-3 py-1.5 rounded ${pingStatus.status === 'success' ? 'bg-emerald-900/50 text-emerald-400 border border-emerald-800' :
                    pingStatus.status === 'error' ? 'bg-rose-900/50 text-rose-400 border border-rose-800' :
                      'bg-slate-800 text-slate-300'
                    }`}>
                    {pingStatus.message}
                  </span>
                )}
              </div>
            </div>

            {/* High-Fidelity Input Controller */}
            <CommandControllerPanel onDispatch={(query) => {
              setPrompt(query);
              triggerPlanner(query);
            }} />


            {/* Core Interface Status Grid */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

              <Card className="bg-slate-900/60 border-slate-850 hover:border-slate-800 transition-colors">
                <CardHeader className="p-4 pb-2 flex flex-row items-center justify-between">
                  <CardTitle className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Keyword Engine
                  </CardTitle>
                  <Database className="h-4 w-4 text-cyan-400" />
                </CardHeader>
                <CardContent className="p-4 pt-0">
                  <div className="text-xl font-bold font-mono text-white">SQLite Active</div>
                  <p className="text-[10px] text-emerald-400 flex items-center gap-1 mt-1 font-mono">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    keyword_store.db linked
                  </p>
                </CardContent>
              </Card>

              <Card className="bg-slate-900/60 border-slate-850 hover:border-slate-800 transition-colors">
                <CardHeader className="p-4 pb-2 flex flex-row items-center justify-between">
                  <CardTitle className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Vector Store
                  </CardTitle>
                  <Network className="h-4 w-4 text-purple-400" />
                </CardHeader>
                <CardContent className="p-4 pt-0">
                  <div className="text-xl font-bold font-mono text-white">Qdrant Online</div>
                  <p className="text-[10px] text-emerald-400 flex items-center gap-1 mt-1 font-mono">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    dim: 384 vectors indexed
                  </p>
                </CardContent>
              </Card>

              <Card className="bg-slate-900/60 border-slate-850 hover:border-slate-800 transition-colors">
                <CardHeader className="p-4 pb-2 flex flex-row items-center justify-between">
                  <CardTitle className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Session Memory
                  </CardTitle>
                  <Cpu className="h-4 w-4 text-amber-400" />
                </CardHeader>
                <CardContent className="p-4 pt-0">
                  <div className="text-xl font-bold font-mono text-white">Redis Cache</div>
                  <p className="text-[10px] text-emerald-400 flex items-center gap-1 mt-1 font-mono">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    Session states managed
                  </p>
                </CardContent>
              </Card>

            </div>

            {analyzedResults && analyzedResults.length > 0 && (
              <div className="mt-6">
                <h3 className="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2 font-mono">
                  <Database className="h-4 w-4 text-emerald-400" />
                  Execution Output Analytics
                </h3>
                <AnalyticsDisplayGrid results={analyzedResults} />
              </div>
            )}

            {result && (
              <div className="mt-6">
                <h3 className="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2 font-mono">
                  <Search className="h-4 w-4 text-cyan-400" />
                  Extracted Details
                </h3>
                <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 text-slate-300 font-mono text-xs whitespace-pre-wrap max-h-[400px] overflow-y-auto scrollbar-thin scrollbar-thumb-slate-800 break-words">
                  {result}
                </div>
              </div>
            )}

          </div>

          {/* Right Column (Agent Terminal WebSocket Monitor) */}
          <div className="space-y-6">
            <Card className="bg-slate-950 border-slate-850 h-[500px] flex flex-col shadow-2xl relative">
              <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-cyan-500/20 to-transparent" />

              <CardHeader className="border-b border-slate-900 p-4 shrink-0 flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-sm font-bold flex items-center gap-2 text-slate-200">
                    <Terminal className="h-4 w-4 text-cyan-400" />
                    Agent Terminal
                  </CardTitle>
                  <CardDescription className="text-[10px] text-slate-500 font-mono">
                    Live planner execution telemetry
                  </CardDescription>
                </div>
                {isRunning && (
                  <span className="flex h-2 w-2 relative">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
                  </span>
                )}
              </CardHeader>

              <CardContent className="p-4 flex-1 overflow-y-auto font-mono text-xs space-y-4 scrollbar-thin scrollbar-thumb-slate-800">
                {logs.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-slate-600 gap-2">
                    <Terminal className="h-8 w-8 opacity-20" />
                    <p className="text-center text-[10px]">No active telemetry stream.<br />Enter a prompt and click "Run Plan".</p>
                  </div>
                ) : (
                  logs.map((log, index) => {
                    const isCompleted = log.status === "completed";
                    const isFailed = log.status === "failed";
                    const isRunning = log.status === "running";

                    return (
                      <div
                        key={index}
                        className={`p-3 rounded-lg border transition-all ${isFailed
                          ? "bg-rose-950/20 border-rose-900/30 text-rose-200"
                          : isCompleted
                            ? "bg-slate-900/40 border-slate-800/40 text-slate-300"
                            : isRunning
                              ? "bg-cyan-950/20 border-cyan-900/40 text-cyan-200 shadow-md shadow-cyan-950/10"
                              : "bg-slate-900/10 border-transparent text-slate-500"
                          }`}
                      >
                        <div className="flex items-center justify-between font-bold mb-1">
                          <span className="flex items-center gap-1.5">
                            {isFailed && <AlertCircle className="h-3.5 w-3.5 text-rose-500 shrink-0" />}
                            {isCompleted && <CheckCircle className="h-3.5 w-3.5 text-emerald-500 shrink-0" />}
                            {isRunning && <RefreshCw className="h-3.5 w-3.5 text-cyan-400 animate-spin shrink-0" />}
                            {!isFailed && !isCompleted && !isRunning && <span className="h-1.5 w-1.5 rounded-full bg-slate-600 shrink-0" />}
                            {log.step}
                          </span>
                          <span className="text-[10px] text-slate-500 font-normal">{log.timestamp}</span>
                        </div>
                        {log.details && (
                          <p className="text-[11px] leading-relaxed text-slate-400 pl-5">
                            {log.details}
                          </p>
                        )}
                      </div>
                    );
                  })
                )}
                <div ref={logsEndRef} />
              </CardContent>
            </Card>
          </div>

        </div>

      </div>
    </main>
  );
}