"use client";

import { useEffect, useState } from "react";
import { Check, X, Clock, MessageSquare, Inbox, Search, AlertCircle, Bot, CheckCircle2, XCircle, Trash2, Lock, LogOut } from "lucide-react";
import { auth } from "../../lib/firebase";
import { signInWithEmailAndPassword, onAuthStateChanged, signOut, User } from "firebase/auth";

interface PendingEvaluation {
  id: string;
  topic: string;
  queries: string[];
  ai_response: string;
  count: number;
  timestamp: string;
  scenario: string;
  status: string;
}

export default function AdminDashboard() {
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");

  const [evaluations, setEvaluations] = useState<PendingEvaluation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  
  // Tabs: pending, answered
  const [activeTab, setActiveTab] = useState<"pending" | "answered">("pending");

  const API_BASE = "http://localhost:8000";

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setAuthLoading(false);
      if (u) {
        fetchEvaluations(activeTab);
      }
    });
    return () => unsubscribe();
  }, [activeTab]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError("");
    try {
      await signInWithEmailAndPassword(auth, email, password);
    } catch (err: any) {
      if (err.code === "auth/operation-not-allowed") {
        setLoginError("Email/Password Auth is disabled. Please enable it in Firebase Console.");
      } else {
        setLoginError("Invalid credentials");
      }
    }
  };

  const fetchEvaluations = async (status: string) => {
    try {
      setLoading(true);
      setError("");
      const res = await fetch(`${API_BASE}/api/admin/evaluations?status=${status}`);
      if (!res.ok) throw new Error(`Failed to fetch ${status} evaluations`);
      const json = await res.json();
      setEvaluations(json.data || []);
      if (json.data && json.data.length > 0) {
        setSelectedId(json.data[0].id);
      } else {
        setSelectedId(null);
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleEvaluate = async (id: string, action: "approve" | "reject") => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: id, action }),
      });
      if (!res.ok) throw new Error(`Failed to ${action}`);
      
      if (activeTab === "pending") {
        // Remove from pending list
        const newEvals = evaluations.filter((item) => item.id !== id);
        setEvaluations(newEvals);
        if (selectedId === id) {
          setSelectedId(newEvals.length > 0 ? newEvals[0].id : null);
        }
      } else {
        // In Answered tab, just update the status locally
        setEvaluations(prev => prev.map(item => 
          item.id === id ? { ...item, status: `${action}ed` } : item
        ));
      }
    } catch (err: any) {
      alert("Error: " + err.message);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to permanently delete this record? The AI will completely forget this.")) return;
    try {
      const res = await fetch(`${API_BASE}/api/admin/evaluate/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Failed to delete");
      
      const newEvals = evaluations.filter((item) => item.id !== id);
      setEvaluations(newEvals);
      if (selectedId === id) {
        setSelectedId(newEvals.length > 0 ? newEvals[0].id : null);
      }
    } catch (err: any) {
      alert("Error: " + err.message);
    }
  };

  if (authLoading) return <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-zinc-500">Loading...</div>;

  if (!user) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center font-sans selection:bg-indigo-500/30">
        <form onSubmit={handleLogin} className="w-full max-w-sm bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 shadow-2xl">
          <div className="flex justify-center mb-6">
            <div className="w-12 h-12 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
              <Lock size={24} className="text-indigo-400" />
            </div>
          </div>
          <h2 className="text-xl font-bold text-zinc-100 text-center mb-2">Admin Login</h2>
          <p className="text-sm text-zinc-500 text-center mb-8">Sign in to access the knowledge base inbox.</p>
          
          {loginError && (
            <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs text-center">
              {loginError}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Email</label>
              <input type="email" required value={email} onChange={e => setEmail(e.target.value)} className="w-full bg-[#0a0a0a] border border-zinc-800 text-sm text-zinc-200 rounded-lg px-4 py-2.5 focus:outline-none focus:border-indigo-500/50 transition-all" placeholder="admin@admin.com" />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Password</label>
              <input type="password" required value={password} onChange={e => setPassword(e.target.value)} className="w-full bg-[#0a0a0a] border border-zinc-800 text-sm text-zinc-200 rounded-lg px-4 py-2.5 focus:outline-none focus:border-indigo-500/50 transition-all" placeholder="••••••••" />
            </div>
          </div>
          
          <button type="submit" className="w-full mt-8 bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-sm py-2.5 rounded-lg transition-all shadow-[0_0_15px_rgba(79,70,229,0.3)]">
            Sign In
          </button>
        </form>
      </div>
    );
  }

  const filteredEvaluations = evaluations.filter(e => 
    e.topic.toLowerCase().includes(searchQuery.toLowerCase()) || 
    e.queries.some(q => q.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const selectedEval = evaluations.find(e => e.id === selectedId);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-zinc-200 flex flex-col font-sans selection:bg-indigo-500/30">
      {/* Header */}
      <header className="h-16 border-b border-zinc-800/60 bg-[#0a0a0a]/80 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-10 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
            <Bot size={18} className="text-indigo-400" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-zinc-100 leading-tight">Knowledge Base Inbox</h1>
            <p className="text-[11px] text-zinc-500 uppercase tracking-wider font-medium">HITL Verification</p>
          </div>
        </div>
        
        {/* Tabs & Logout */}
        <div className="flex items-center gap-4">
          <div className="flex items-center bg-zinc-900/50 p-1 rounded-lg border border-zinc-800/60">
            <button 
              onClick={() => setActiveTab("pending")}
              className={`px-6 py-1.5 text-xs font-medium rounded-md transition-all ${activeTab === "pending" ? "bg-zinc-800 text-zinc-200 shadow-sm" : "text-zinc-500 hover:text-zinc-300"}`}
            >
              Unanswered
            </button>
            <button 
              onClick={() => setActiveTab("answered")}
              className={`px-6 py-1.5 text-xs font-medium rounded-md transition-all ${activeTab === "answered" ? "bg-indigo-500/20 text-indigo-400 shadow-sm" : "text-zinc-500 hover:text-zinc-300"}`}
            >
              Answered
            </button>
          </div>
          <button 
            onClick={() => signOut(auth)}
            className="p-2 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-900 rounded-lg transition-all"
            title="Sign Out"
          >
            <LogOut size={16} />
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - List */}
        <div className="w-80 border-r border-zinc-800/60 bg-[#0a0a0a] flex flex-col shrink-0">
          <div className="p-4 border-b border-zinc-800/60 shrink-0">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input
                type="text"
                placeholder="Search topics or queries..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-zinc-900/50 border border-zinc-800 text-sm text-zinc-200 rounded-lg pl-9 pr-4 py-2 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all"
              />
            </div>
          </div>
          
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex justify-center p-8">
                <div className="animate-pulse flex items-center space-x-2">
                  <div className="w-2 h-2 rounded-full bg-indigo-500"></div>
                  <div className="w-2 h-2 rounded-full bg-indigo-500"></div>
                  <div className="w-2 h-2 rounded-full bg-indigo-500"></div>
                </div>
              </div>
            ) : filteredEvaluations.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-8 text-center h-full">
                {activeTab === "pending" ? (
                  <Inbox size={24} className="text-zinc-700 mb-3" />
                ) : (
                  <CheckCircle2 size={24} className="text-zinc-700 mb-3" />
                )}
                <p className="text-zinc-500 text-sm">No {activeTab} items found.</p>
              </div>
            ) : (
              <ul className="divide-y divide-zinc-800/30">
                {filteredEvaluations.map((item) => {
                  const isSelected = selectedId === item.id;
                  const isApp = item.status === "approved";
                  const isRej = item.status === "rejected";
                  return (
                    <li key={item.id}>
                      <button
                        onClick={() => setSelectedId(item.id)}
                        className={`w-full text-left px-5 py-4 transition-all hover:bg-zinc-900/50 relative ${
                          isSelected ? "bg-zinc-900" : ""
                        }`}
                      >
                        {isSelected && <div className={`absolute left-0 top-0 bottom-0 w-0.5 ${isApp ? 'bg-indigo-500' : isRej ? 'bg-red-500' : 'bg-zinc-500'}`} />}
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-sm font-semibold text-zinc-200 truncate pr-3">{item.topic.replace(/_/g, " ")}</span>
                          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ${isApp ? 'bg-indigo-500/10 text-indigo-400' : isRej ? 'bg-red-500/10 text-red-400' : 'bg-zinc-500/10 text-zinc-400'}`}>
                            {item.count}x
                          </span>
                        </div>
                        <p className="text-xs text-zinc-500 line-clamp-1 mb-2">
                          {item.queries[0]}
                        </p>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 text-[10px] text-zinc-600">
                            <Clock size={12} />
                            {new Date(item.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                          </div>
                          {activeTab === "answered" && (
                            <span className={`text-[10px] uppercase font-bold tracking-wider ${isApp ? 'text-indigo-400' : 'text-red-400'}`}>
                              {item.status}
                            </span>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Right Panel - Details */}
        <div className="flex-1 bg-[#0a0a0a] flex flex-col overflow-hidden relative">
          {error && (
            <div className="absolute top-4 left-4 right-4 z-20 bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-lg text-sm flex items-center gap-3">
              <AlertCircle size={16} />
              {error}
            </div>
          )}

          {!loading && selectedEval ? (
            <div className="flex-1 overflow-y-auto p-8 md:p-12">
              <div className="max-w-3xl mx-auto">
                <div className="mb-8 flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-3 mb-4">
                      <span className="px-2.5 py-1 rounded-md bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-400 uppercase tracking-wider">
                        {selectedEval.topic}
                      </span>
                      <span className="text-xs text-zinc-600 font-medium">
                        {selectedEval.scenario}
                      </span>
                    </div>
                    <h2 className="text-2xl font-semibold text-zinc-100 leading-tight flex items-center gap-4">
                      {activeTab === "pending" ? "Verification Required" : selectedEval.status === "approved" ? "Approved Answer" : "Rejected Answer"}
                      {activeTab === "answered" && (
                        <span className={`px-3 py-1 rounded-full border text-[10px] font-bold uppercase tracking-widest ${selectedEval.status === 'approved' ? 'bg-indigo-500/10 border-indigo-500/30 text-indigo-400' : 'bg-red-500/10 border-red-500/30 text-red-400'}`}>
                          {selectedEval.status}
                        </span>
                      )}
                    </h2>
                  </div>
                  
                  <div className="flex items-center gap-3 shrink-0">
                    {selectedEval.status !== "rejected" && (
                      <button
                        onClick={() => handleEvaluate(selectedEval.id, "reject")}
                        className="h-10 px-4 flex items-center gap-2 rounded-lg text-xs font-medium text-zinc-300 bg-zinc-900 border border-zinc-800 hover:bg-red-500/10 hover:text-red-400 hover:border-red-500/20 transition-all focus:outline-none"
                      >
                        <X size={14} />
                        {selectedEval.status === "approved" ? "Change to Reject" : "Reject"}
                      </button>
                    )}
                    {selectedEval.status !== "approved" && (
                      <button
                        onClick={() => handleEvaluate(selectedEval.id, "approve")}
                        className="h-10 px-4 flex items-center gap-2 rounded-lg text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 border border-indigo-500 transition-all focus:outline-none shadow-[0_0_10px_rgba(79,70,229,0.3)]"
                      >
                        <Check size={14} />
                        {selectedEval.status === "rejected" ? "Change to Approve" : "Approve"}
                      </button>
                    )}
                    <div className="w-px h-6 bg-zinc-800 mx-1"></div>
                    <button 
                      onClick={() => handleDelete(selectedEval.id)}
                      className="p-2.5 rounded-lg bg-zinc-900 hover:bg-red-500/20 border border-zinc-800 hover:border-red-500/30 text-zinc-400 hover:text-red-400 transition-all focus:outline-none"
                      title="Permanently Delete"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>

                <div className="space-y-8">
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-4 flex items-center gap-2">
                      <MessageSquare size={14} />
                      User Queries ({selectedEval.count})
                    </h3>
                    <div className="bg-zinc-900/30 border border-zinc-800/60 rounded-xl p-1">
                      <ul className="divide-y divide-zinc-800/30">
                        {selectedEval.queries.map((q, idx) => (
                          <li key={idx} className="px-4 py-3 text-sm text-zinc-300 flex gap-4">
                            <span className="text-zinc-600 select-none text-xs mt-0.5 font-mono">{(idx + 1).toString().padStart(2, '0')}</span>
                            <span>{q}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </section>

                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-4 flex items-center gap-2">
                      <Bot size={14} />
                      AI Generated Response
                    </h3>
                    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-6 shadow-sm">
                      <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap font-serif">
                        {selectedEval.ai_response}
                      </p>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          ) : !loading ? (
            <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
              Select an item from the inbox to review
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
