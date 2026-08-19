"use client";

import { useEffect, useState, useRef } from "react";
import {
  Clock,
  MessageSquare,
  Inbox,
  Search,
  AlertCircle,
  Bot,
  CheckCircle2,
  Trash2,
  Lock,
  LogOut,
  Sparkles,
  BookOpen,
  ShieldBan,
  ChevronDown,
  Pen,
  XCircle,
  RotateCcw,
  RefreshCw,
  Database,
} from "lucide-react";
import { auth } from "../../lib/firebase";
import {
  signInWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  User,
} from "firebase/auth";

interface PendingEvaluation {
  id: string;
  topic: string;
  queries: string[];
  ai_response: string;
  count: number;
  timestamp: string;
  scenario: string;
  status: string;
  admin_note?: string;
  official_answer?: string;
}

interface CoachForm {
  adminNote: string;
  submitting: boolean;
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

  // Tabs
  const [activeTab, setActiveTab] = useState<"pending" | "answered">("pending");

  // Sync state
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState(0);
  const [syncStatus, setSyncStatus] = useState("");

  const startSync = () => {
    if (syncing) return;
    setSyncing(true);
    setSyncProgress(0);
    setSyncStatus("Starting Pinecone sync...");

    const eventSource = new EventSource(
      process.env.NODE_ENV === "development" 
        ? "http://localhost:8000/api/sync-kb/stream" 
        : "/api/sync-kb/stream"
    );

    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setSyncProgress(data.progress || 0);
        setSyncStatus(data.status || "");

        if (data.is_done) {
          eventSource.close();
          setSyncing(false);
          if (data.error) {
            showToast("Sync Error: " + data.status, "error");
          } else {
            const sum = data.summary || {};
            showToast(`✓ Sync Complete! Added: ${sum.added || 0}, Updated: ${sum.updated || 0}`, "success");
          }
        }
      } catch (err) {
        console.error("Failed to parse SSE data:", err);
      }
    };

    eventSource.onerror = (e) => {
      eventSource.close();
      setSyncing(false);
      showToast("Connection to sync server lost.", "error");
    };
  };

  // Coaching form state — keyed by evaluation id
  const [coachFormOpen, setCoachFormOpen] = useState<string | null>(null);
  const [coachForms, setCoachForms] = useState<Record<string, CoachForm>>({});

  // Toast notifications
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);


  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setAuthLoading(false);
      if (u) {
        fetchEvaluations(activeTab);
      }
    });
    return () => unsubscribe();
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
      const res = await fetch(`/api/admin/evaluations?status=${status}`);
      if (!res.ok) throw new Error(`Failed to fetch ${status} evaluations`);
      const json = await res.json();
      setEvaluations(json.data || []);
      setSelectedId(json.data?.length > 0 ? json.data[0].id : null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };


  const showToast = (message: string, type: "success" | "error" = "success") => {
    setToast({ message, type });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  };

  // ── Open coaching form ──────────────────────────────────────────────────────
  const openCoachForm = (evalItem: PendingEvaluation) => {
    if (!coachForms[evalItem.id]) {
      setCoachForms((prev) => ({
        ...prev,
        [evalItem.id]: {
          adminNote: evalItem.admin_note ?? "",
          submitting: false,
        },
      }));
    }
    setCoachFormOpen(evalItem.id);
  };

  const closeCoachForm = () => setCoachFormOpen(null);

  const updateCoachForm = (id: string, patch: Partial<CoachForm>) => {
    setCoachForms((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  };

  // ── Inject to Knowledge Base ────────────────────────────────────────────────
  const handleInjectKB = async (evalItem: PendingEvaluation) => {
    const form = coachForms[evalItem.id];
    if (!form) return;

    updateCoachForm(evalItem.id, { submitting: true });
    try {
      const res = await fetch("/api/admin/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          doc_id: evalItem.id,
          action: "inject_kb",
          admin_note: form.adminNote,
        }),
      });
      if (!res.ok) throw new Error("Failed to inject into knowledge base");

      if (activeTab === "pending") {
        const newEvals = evaluations.filter((e) => e.id !== evalItem.id);
        setEvaluations(newEvals);
        setSelectedId(newEvals.length > 0 ? newEvals[0].id : null);
      } else {
        setEvaluations((prev) =>
          prev.map((e) =>
            e.id === evalItem.id
              ? { ...e, status: "approved", admin_note: form.adminNote }
              : e
          )
        );
      }
      closeCoachForm();
      showToast("✓ Coaching instruction saved & injected into knowledge base.");
    } catch (err: any) {
      showToast("Error: " + err.message, "error");
    } finally {
      updateCoachForm(evalItem.id, { submitting: false });
    }
  };

  // ── Mark as Spam ────────────────────────────────────────────────────────────
  const handleSpam = async (id: string) => {
    try {
      const res = await fetch("/api/admin/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: id, action: "spam" }),
      });
      if (!res.ok) throw new Error("Failed to mark as spam");

      const newEvals = evaluations.filter((e) => e.id !== id);
      setEvaluations(newEvals);
      setSelectedId(newEvals.length > 0 ? newEvals[0].id : null);
      closeCoachForm();
      showToast("Entry marked as spam and ignored.");
    } catch (err: any) {
      showToast("Error: " + err.message, "error");
    }
  };

  // ── Permanent delete ────────────────────────────────────────────────────────
  const handleDelete = async (id: string) => {
    if (!confirm("Permanently delete this record? The AI will completely forget this.")) return;
    try {
      const res = await fetch(`/api/admin/evaluate/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete");
      const newEvals = evaluations.filter((e) => e.id !== id);
      setEvaluations(newEvals);
      if (selectedId === id) setSelectedId(newEvals.length > 0 ? newEvals[0].id : null);
      showToast("Record permanently deleted.");
    } catch (err: any) {
      showToast("Error: " + err.message, "error");
    }
  };


  if (authLoading) return (
    <div className="min-h-screen bg-[#080810] flex items-center justify-center">
      <div className="flex items-center gap-2">
        <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.3s]" />
        <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.15s]" />
        <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" />
      </div>
    </div>
  );

  if (!user) {
    return (
      <div className="min-h-screen bg-[#080810] flex items-center justify-center font-sans selection:bg-indigo-500/30">
        <form onSubmit={handleLogin} className="w-full max-w-sm bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 shadow-2xl">
          <div className="flex justify-center mb-6">
            <div className="w-12 h-12 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
              <Lock size={22} className="text-indigo-400" />
            </div>
          </div>
          <h2 className="text-xl font-bold text-zinc-100 text-center mb-1">Admin Login</h2>
          <p className="text-sm text-zinc-500 text-center mb-8">Sign in to access the AI coaching dashboard.</p>

          {loginError && (
            <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs text-center flex items-center gap-2 justify-center">
              <AlertCircle size={13} />
              {loginError}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Email</label>
              <input type="email" required value={email} onChange={e => setEmail(e.target.value)} className="w-full bg-[#0a0a0a] border border-zinc-800 text-sm text-zinc-200 rounded-lg px-4 py-2.5 focus:outline-none focus:border-indigo-500/50 transition-all" placeholder="admin@company.com" />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Password</label>
              <input type="password" required value={password} onChange={e => setPassword(e.target.value)} className="w-full bg-[#0a0a0a] border border-zinc-800 text-sm text-zinc-200 rounded-lg px-4 py-2.5 focus:outline-none focus:border-indigo-500/50 transition-all" placeholder="••••••••" />
            </div>
          </div>

          <button type="submit" className="w-full mt-8 bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-sm py-2.5 rounded-lg transition-all shadow-[0_0_20px_rgba(79,70,229,0.25)]">
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
  const isCoachOpen = selectedEval ? coachFormOpen === selectedEval.id : false;
  const currentForm = selectedEval ? coachForms[selectedEval.id] : null;

  return (
    <div className="min-h-screen bg-[#080810] text-zinc-200 flex flex-col font-sans selection:bg-indigo-500/30">

      {/* ── Toast ─────────────────────────────────────────────────────────────── */}
      <div className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 ${toast ? "opacity-100 translate-y-0" : "opacity-0 translate-y-3 pointer-events-none"}`}>
        <div className={`flex items-center gap-3 px-5 py-3 rounded-xl text-sm font-medium shadow-2xl border backdrop-blur-sm ${toast?.type === "error" ? "bg-red-950/80 border-red-500/30 text-red-300" : "bg-zinc-900/90 border-zinc-700/60 text-zinc-200"}`}>
          {toast?.type === "error"
            ? <XCircle size={15} className="text-red-400 shrink-0" />
            : <CheckCircle2 size={15} className="text-emerald-400 shrink-0" />}
          {toast?.message}
        </div>
      </div>

      {/* ── Header ────────────────────────────────────────────────────────────── */}
      <header className="h-14 border-b border-zinc-800/60 bg-[#080810]/90 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-10 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
            <Sparkles size={15} className="text-indigo-400" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-zinc-100 leading-tight">PBG Assist — AI Coach</h1>
            <p className="text-[10px] text-zinc-600 uppercase tracking-widest font-medium">Knowledge Base Inbox</p>
          </div>
        </div>

        <div className="flex items-center gap-3">


          <div className="w-px h-6 bg-zinc-800/80 mx-1"></div>

          <div className="flex items-center bg-zinc-900/50 p-1 rounded-lg border border-zinc-800/60">
            <button
              onClick={() => setActiveTab("pending")}
              className={`px-5 py-1.5 text-xs font-medium rounded-md transition-all ${activeTab === "pending" ? "bg-zinc-800 text-zinc-100 shadow-sm" : "text-zinc-500 hover:text-zinc-300"}`}
            >
              Unanswered
              {activeTab === "pending" && evaluations.length > 0 && (
                <span className="ml-2 bg-indigo-500/20 text-indigo-400 text-[10px] font-bold px-1.5 py-0.5 rounded-full">{evaluations.length}</span>
              )}
            </button>
            <button
              onClick={() => setActiveTab("answered")}
              className={`px-5 py-1.5 text-xs font-medium rounded-md transition-all ${activeTab === "answered" ? "bg-zinc-800 text-zinc-100 shadow-sm" : "text-zinc-500 hover:text-zinc-300"}`}
            >
              Answered
            </button>
          </div>
          <button onClick={() => signOut(auth)} className="p-2 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-900 rounded-lg transition-all" title="Sign Out">
            <LogOut size={15} />
          </button>
        </div>
      </header>

      {/* ── Sync Progress Banner ──────────────────────────────────────────────── */}
      {syncing && (
        <div className="bg-zinc-900/80 border-b border-zinc-800/60 px-6 py-3 flex flex-col gap-2 shrink-0">
          <div className="flex justify-between items-center text-xs text-zinc-400">
            <span className="font-medium text-indigo-400">{syncStatus}</span>
            <span>{syncProgress}%</span>
          </div>
          <div className="w-full bg-zinc-950 rounded-full h-1.5 border border-zinc-800/50 overflow-hidden">
            <div
              className="bg-indigo-500 h-1.5 rounded-full transition-all duration-300 ease-out relative"
              style={{ width: `${syncProgress}%` }}
            >
              <div className="absolute top-0 left-0 right-0 bottom-0 bg-white/20 animate-pulse"></div>
            </div>
          </div>
        </div>
      )}

      {/* ── Main ──────────────────────────────────────────────────────────────── */}
      <div className="flex-1 flex overflow-hidden">

        {/* Left Sidebar */}
        <div className="w-72 border-r border-zinc-800/60 bg-[#080810] flex flex-col shrink-0">
          <div className="p-3 border-b border-zinc-800/60 shrink-0">
            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600" />
              <input
                type="text"
                placeholder="Search topics or queries…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-zinc-900/50 border border-zinc-800 text-xs text-zinc-200 rounded-lg pl-8 pr-4 py-2 focus:outline-none focus:border-indigo-500/40 focus:ring-1 focus:ring-indigo-500/20 transition-all placeholder:text-zinc-600"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex justify-center p-10">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.3s]" />
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.15s]" />
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" />
                </div>
              </div>
            ) : filteredEvaluations.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-8 text-center h-full gap-3">
                {activeTab === "pending" ? <Inbox size={22} className="text-zinc-700" /> : <CheckCircle2 size={22} className="text-zinc-700" />}
                <p className="text-zinc-600 text-xs">No {activeTab} items found.</p>
              </div>
            ) : (
              <ul className="divide-y divide-zinc-800/20">
                {filteredEvaluations.map((item) => {
                  const isSelected = selectedId === item.id;
                  const isApproved = item.status === "approved";
                  const isRejected = item.status === "rejected";
                  return (
                    <li key={item.id}>
                      <button
                        onClick={() => { setSelectedId(item.id); closeCoachForm(); }}
                        className={`w-full text-left px-4 py-3.5 transition-all hover:bg-zinc-900/40 relative ${isSelected ? "bg-zinc-900/60" : ""}`}
                      >
                        {isSelected && (
                          <div className={`absolute left-0 top-0 bottom-0 w-0.5 rounded-r ${isApproved ? "bg-emerald-500" : isRejected ? "bg-red-500/70" : "bg-indigo-500"}`} />
                        )}
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-semibold text-zinc-200 truncate pr-2">{item.topic.replace(/_/g, " ")}</span>
                          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0 bg-zinc-800 text-zinc-500">{item.count}×</span>
                        </div>
                        <p className="text-[11px] text-zinc-600 line-clamp-1 mb-2">{item.queries[0]}</p>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5 text-[10px] text-zinc-700">
                            <Clock size={11} />
                            {new Date(item.timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                          </div>
                          {activeTab === "answered" && (
                            <span className={`text-[9px] uppercase font-bold tracking-widest ${isApproved ? "text-emerald-500" : "text-red-400"}`}>
                              {isApproved ? "Injected" : "Spam"}
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

        {/* Right Panel */}
        <div className="flex-1 bg-[#080810] flex flex-col overflow-hidden relative">
          {error && (
            <div className="absolute top-4 left-4 right-4 z-20 bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-lg text-sm flex items-center gap-3">
              <AlertCircle size={15} />
              {error}
            </div>
          )}

          {!loading && selectedEval ? (
            <div className="flex-1 overflow-y-auto p-8 lg:p-12">
              <div className="max-w-3xl mx-auto space-y-8">

                {/* ── Page Header ─────────────────────────────────────────────── */}
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <span className="px-2.5 py-1 rounded-md bg-zinc-900 border border-zinc-800 text-[11px] font-mono text-zinc-500 uppercase tracking-wider">
                        {selectedEval.topic}
                      </span>
                      <span className="text-[11px] text-zinc-600">{selectedEval.scenario}</span>
                    </div>
                    <h2 className="text-2xl font-bold text-zinc-100 leading-snug">
                      {activeTab === "pending" ? "Review AI Response" : "Answered Entry"}
                    </h2>
                    <p className="text-sm text-zinc-500 mt-1">
                      {activeTab === "pending"
                        ? "Coach the AI on how it should answer this type of question."
                        : "This entry has already been processed."}
                    </p>
                  </div>

                  <div className="flex items-center gap-2 shrink-0 pt-1">
                    {activeTab === "pending" && !isCoachOpen && (
                      <button
                        onClick={() => openCoachForm(selectedEval)}
                        className="h-9 px-4 flex items-center gap-2 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 border border-indigo-500/50 transition-all shadow-[0_0_16px_rgba(79,70,229,0.3)] focus:outline-none"
                      >
                        <Pen size={13} />
                        Resolve &amp; Coach AI
                      </button>
                    )}
                    {isCoachOpen && (
                      <button
                        onClick={closeCoachForm}
                        className="h-9 px-4 flex items-center gap-2 rounded-lg text-xs font-medium text-zinc-400 bg-zinc-900 border border-zinc-800 hover:text-zinc-200 transition-all focus:outline-none"
                      >
                        <RotateCcw size={13} />
                        Collapse
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(selectedEval.id)}
                      className="h-9 w-9 flex items-center justify-center rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 hover:border-red-500/20 transition-all focus:outline-none"
                      title="Permanently Delete"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* ── User Queries ─────────────────────────────────────────────── */}
                <section>
                  <h3 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 mb-3 flex items-center gap-2">
                    <MessageSquare size={13} />
                    User Queries
                    <span className="bg-zinc-800 text-zinc-500 text-[10px] font-bold px-1.5 py-0.5 rounded-full">{selectedEval.count}×</span>
                  </h3>
                  <div className="bg-zinc-900/30 border border-zinc-800/50 rounded-xl overflow-hidden">
                    <ul className="divide-y divide-zinc-800/30">
                      {selectedEval.queries.map((q, idx) => (
                        <li key={idx} className="px-5 py-3.5 text-sm text-zinc-300 flex gap-4">
                          <span className="text-zinc-700 select-none text-[11px] mt-0.5 font-mono tabular-nums shrink-0">{(idx + 1).toString().padStart(2, "0")}</span>
                          <span className="leading-relaxed">{q}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </section>

                {/* ── AI Generated Response ────────────────────────────────────── */}
                <section>
                  <h3 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 mb-3 flex items-center gap-2">
                    <Bot size={13} />
                    AI Generated Response
                    {activeTab === "pending" && (
                      <span className="text-[10px] text-amber-500/80 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">Needs Review</span>
                    )}
                  </h3>
                  <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-6">
                    <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                      {selectedEval.ai_response}
                    </p>
                  </div>
                </section>

                {/* ── Previous Coaching (answered tab) ────────────────────────── */}
                {activeTab === "answered" && selectedEval.admin_note && (
                  <section>
                    <h3 className="text-[11px] font-semibold uppercase tracking-widest text-emerald-500/80 mb-3 flex items-center gap-2">
                      <BookOpen size={13} />
                      Admin Coaching Applied
                    </h3>
                    <div className="border border-emerald-500/15 bg-emerald-500/5 rounded-xl p-5 space-y-3">
                      <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-500/60 mb-1.5">Coaching Note</p>
                      <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">{selectedEval.admin_note}</p>
                      <div className="flex items-start gap-2 pt-1 border-t border-emerald-500/10">
                        <Bot size={13} className="text-indigo-400 shrink-0 mt-0.5" />
                        <p className="text-[11px] text-zinc-600">The AI will generate its response guided by this instruction — no verbatim answer was stored.</p>
                      </div>
                    </div>
                  </section>
                )}

                {/* ── Coaching Form ────────────────────────────────────────────── */}
                {isCoachOpen && currentForm && (
                  <section className="border border-indigo-500/20 bg-indigo-500/[0.04] rounded-2xl overflow-hidden shadow-[0_0_40px_rgba(79,70,229,0.06)]">

                    {/* Form Header */}
                    <div className="flex items-center justify-between px-6 py-4 border-b border-indigo-500/15 bg-indigo-500/5">
                      <div className="flex items-center gap-3">
                        <div className="w-7 h-7 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center">
                          <Sparkles size={13} className="text-indigo-400" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-zinc-100">AI Coaching Instruction</p>
                          <p className="text-[11px] text-zinc-500">Tell the AI <em>how</em> to respond. It will generate its own answer guided by your note.</p>
                        </div>
                      </div>
                      <ChevronDown size={16} className="text-zinc-600 cursor-pointer hover:text-zinc-400 transition-colors" onClick={closeCoachForm} />
                    </div>

                    <div className="p-6 space-y-5">

                      {/* Coaching Note — only field */}
                      <div>
                        <label className="flex items-center gap-2 text-xs font-semibold text-zinc-300 mb-1.5">
                          <span className="w-5 h-5 rounded-full bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-400 text-[10px] font-bold shrink-0">✍</span>
                          Admin Coaching Note
                          <span className="text-zinc-600 font-normal">(INSTRUKSI ADMIN)</span>
                        </label>
                        <p className="text-[11px] text-zinc-600 mb-2 ml-7">
                          Instruct the AI on <em>how</em> it should respond to questions like this. The AI will craft its own answer following your guidance.
                        </p>
                        <textarea
                          id="coaching-note"
                          rows={5}
                          value={currentForm.adminNote}
                          onChange={(e) => updateCoachForm(selectedEval.id, { adminNote: e.target.value })}
                          placeholder="e.g., Tolak dengan halus, arahkan user ke loket 3, jelaskan bahwa kita hanya melayani proses PBG, gunakan format yang ramah..."
                          className="w-full bg-[#080810] border border-zinc-800 focus:border-amber-500/30 focus:ring-1 focus:ring-amber-500/10 text-sm text-zinc-200 placeholder:text-zinc-700 rounded-xl px-4 py-3 resize-none transition-all outline-none leading-relaxed"
                        />
                      </div>

                      {/* Info callout — explains the AI will self-generate */}
                      <div className="flex items-start gap-3 bg-zinc-900/50 border border-zinc-800/60 rounded-xl p-4">
                        <Bot size={15} className="text-indigo-400 shrink-0 mt-0.5" />
                        <p className="text-[11px] text-zinc-500 leading-relaxed">
                          <span className="text-zinc-300 font-medium">The AI will generate the answer itself.</span>{" "}
                          Your coaching note will be injected as a high-priority instruction into future conversations about this topic. The AI response will <em>not</em> be copied verbatim.
                        </p>
                      </div>

                      {/* Golden Chunk Preview */}
                      {currentForm.adminNote && (
                        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
                          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-600 mb-2 flex items-center gap-2">
                            <BookOpen size={11} />
                            Coaching Chunk Preview
                          </p>
                          <p className="text-[11px] text-zinc-500 font-mono leading-relaxed break-words">
                            <span className="text-indigo-400/70">PERTANYAAN:</span>{" "}
                            {selectedEval.queries.join(" / ")}{" "}
                            <span className="text-amber-400/70">| INSTRUKSI ADMIN:</span>{" "}
                            {currentForm.adminNote}
                          </p>
                        </div>
                      )}

                      {/* Action Buttons */}
                      <div className="flex items-center gap-3 pt-1">
                        <button
                          id="save-inject-btn"
                          onClick={() => handleInjectKB(selectedEval)}
                          disabled={currentForm.submitting || !currentForm.adminNote.trim()}
                          className="flex-1 h-11 flex items-center justify-center gap-2 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-[0_0_20px_rgba(79,70,229,0.2)] focus:outline-none"
                        >
                          {currentForm.submitting ? (
                            <>
                              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                              Saving…
                            </>
                          ) : (
                            <>
                              <BookOpen size={15} />
                              Save Coaching Instruction
                            </>
                          )}
                        </button>

                        <button
                          id="spam-btn"
                          onClick={() => handleSpam(selectedEval.id)}
                          disabled={currentForm.submitting}
                          className="h-11 px-4 flex items-center gap-2 rounded-xl text-sm font-medium text-zinc-400 bg-zinc-900 border border-zinc-800 hover:text-red-400 hover:bg-red-500/10 hover:border-red-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all focus:outline-none"
                        >
                          <ShieldBan size={15} />
                          Mark as Spam / Ignore
                        </button>
                      </div>
                    </div>
                  </section>
                )}

                {/* Prompt when form is closed on pending tab */}
                {activeTab === "pending" && !isCoachOpen && (
                  <div className="rounded-xl border border-dashed border-zinc-800/80 p-5 text-center">
                    <p className="text-xs text-zinc-600">
                      Click{" "}
                      <span className="text-indigo-400 font-semibold">Resolve &amp; Coach AI</span>{" "}
                      above to provide coaching instructions and an official answer.
                    </p>
                  </div>
                )}

              </div>
            </div>
          ) : !loading ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center gap-4">
              <Inbox size={28} className="text-zinc-800" />
              <p className="text-zinc-600 text-sm">Select an item from the inbox to review</p>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
