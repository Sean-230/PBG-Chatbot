"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useTheme } from "next-themes";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import InteractiveDotBackground from "@/components/InteractiveDotBackground";
import {
  Building2,
  Send,
  MessageSquare,
  Mic,
  Volume2,
  VolumeX,
  Sun,
  Moon,
  Plus,
  History,
  Pin,
  PinOff,
  Pencil,
  Check,
  X,
  Trash2,
  FileText,
  Search,
  Clock,
  FolderOpen,
} from "lucide-react";

/* ────────────────────────────────────────────────────────────────────────── */
/*  Constants                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */
const STORAGE_KEY = "pbg-chat-sessions";
const FOURTEEN_DAYS_MS = 1_209_600_000; // 14 days in milliseconds
const HEALTH_POLL_MS = 15_000; // 15 seconds

/* ────────────────────────────────────────────────────────────────────────── */
/*  Types                                                                      */
/* ────────────────────────────────────────────────────────────────────────── */
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  lastUpdatedAt: number;
  isPinned?: boolean;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  localStorage helpers                                                       */
/* ────────────────────────────────────────────────────────────────────────── */
function loadAllSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // Optional migration from older version
      const oldRaw = localStorage.getItem("pbg-chat-history");
      if (oldRaw) {
        try {
          const oldParsed = JSON.parse(oldRaw);
          if (oldParsed && Array.isArray(oldParsed.messages) && oldParsed.messages.length > 0) {
            const migratedSession: ChatSession = {
              id: crypto.randomUUID(),
              title: oldParsed.messages.find((m: any) => m.role === 'user')?.content.substring(0, 30) || "Topik Baru",
              messages: oldParsed.messages,
              lastUpdatedAt: oldParsed.timestamp || Date.now()
            };
            localStorage.removeItem("pbg-chat-history");
            return [migratedSession];
          }
        } catch {}
      }
      return [];
    }

    let parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      localStorage.removeItem(STORAGE_KEY);
      return [];
    }
    
    // Prune older than 14 days UNLESS pinned
    const now = Date.now();
    parsed = parsed.filter((s: ChatSession) => {
      if (s.isPinned) return true;
      return now - s.lastUpdatedAt < FOURTEEN_DAYS_MS;
    });
    
    parsed.sort((a: ChatSession, b: ChatSession) => {
      if (a.isPinned && !b.isPinned) return -1;
      if (!a.isPinned && b.isPinned) return 1;
      return b.lastUpdatedAt - a.lastUpdatedAt;
    });

    // Auto-save the pruned version back
    localStorage.setItem(STORAGE_KEY, JSON.stringify(parsed));
    
    return parsed;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return [];
  }
}

function saveSession(session: ChatSession) {
  if (typeof window === "undefined") return;
  try {
    const sessions = loadAllSessions();
    const existingIdx = sessions.findIndex((s) => s.id === session.id);
    if (existingIdx >= 0) {
      sessions[existingIdx] = session;
    } else {
      sessions.push(session);
    }
    sessions.sort((a, b) => {
      if (a.isPinned && !b.isPinned) return -1;
      if (!a.isPinned && b.isPinned) return 1;
      return b.lastUpdatedAt - a.lastUpdatedAt;
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  } catch {
    // silently ignore
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Custom chat hook — calls FastAPI backend directly via fetch + streaming    */
/* ────────────────────────────────────────────────────────────────────────── */
function usePBGChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isLoading) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };

    const assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsLoading(true);

    try {
      // Build history from current messages + the new user message
      const history = [...messagesRef.current, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
        parts: [],
      }));

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });

      if (!response.ok) {
        throw new Error(`Server responded with status ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error("No response body from server");

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const raw = decoder.decode(value, { stream: true });

        // Backend sends lines like: 0:"text chunk here"\n
        for (const line of raw.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("0:")) continue;
          try {
            const parsed = JSON.parse(trimmed.slice(2)) as string;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + parsed }
                  : m
              )
            );
          } catch {
            // skip malformed lines
          }
        }
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Unknown error";
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: `❌ Terjadi kesalahan koneksi: ${errMsg}` }
            : m
        )
      );
    } finally {
      setIsLoading(false);
    }
  }, [isLoading]);

  return { messages, setMessages, sendMessage, isLoading };
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Typing indicator                                                           */
/* ────────────────────────────────────────────────────────────────────────── */
function TypingIndicator() {
  return (
    <div className="flex items-start gap-3 message-enter">
      <div
        className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center"
        style={{ background: "linear-gradient(135deg,#818cf8,#c084fc)" }}
      >
        <Building2 size={14} color="#fff" />
      </div>
      <div
        className="rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-1.5"
        style={{ background: "var(--bg-ai-bubble)", border: "1px solid var(--border)" }}
      >
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Single message bubble                                                      */
/* ────────────────────────────────────────────────────────────────────────── */
function MessageBubble({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
  const isUser = role === "user";

  return (
    <div
      className={`flex items-end gap-3 message-enter ${isUser ? "flex-row-reverse" : "flex-row"}`}
    >
      {!isUser && (
        <div
          className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center mb-1"
          style={{ background: "linear-gradient(135deg,#818cf8,#c084fc)" }}
        >
          <Building2 size={14} color="#fff" />
        </div>
      )}

      <div
        className={`max-w-[72%] px-4 py-3 text-sm leading-relaxed ${isUser
          ? "rounded-2xl rounded-br-sm text-white"
          : "rounded-2xl rounded-tl-sm"
          }`}
        style={
          isUser
            ? { background: "linear-gradient(135deg, #4338ca 0%, #6d28d9 100%)" }
            : { background: "var(--bg-ai-bubble)", border: "1px solid var(--border)", color: "var(--text-secondary)" }
        }
      >
        {isUser ? (
          <p className="whitespace-pre-wrap m-0">{content}</p>
        ) : (
          <div className="markdown-content text-sm">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({ node, ...props }) => <p className="mb-2 last:mb-0 leading-relaxed" {...props} />,
                ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-2 space-y-1" {...props} />,
                ol: ({ node, ...props }) => <ol className="list-decimal pl-5 mb-2 space-y-1" {...props} />,
                li: ({ node, ...props }) => <li {...props} />,
                h1: ({ node, ...props }) => <h1 className="text-xl font-bold mb-2 mt-4" style={{ color: "var(--text-primary)" }} {...props} />,
                h2: ({ node, ...props }) => <h2 className="text-lg font-bold mb-2 mt-4" style={{ color: "var(--text-primary)" }} {...props} />,
                h3: ({ node, ...props }) => <h3 className="text-md font-bold mb-2 mt-3" style={{ color: "var(--text-primary)" }} {...props} />,
                strong: ({ node, ...props }) => <strong className="font-semibold" style={{ color: "var(--text-primary)" }} {...props} />,
                img: ({ node, ...props }) => <img className="rounded-lg border shadow-sm" style={{ borderColor: "var(--border)", maxHeight: "220px", width: "auto", objectFit: "cover", display: "inline-block" }} {...props} />,
                p: ({ node, children, ...props }) => {
                  // Check if this paragraph contains ONLY images — if so, render as horizontal grid
                  const childArray = Array.isArray(children) ? children : [children];
                  const allImages = childArray.every(
                    (child: any) => child && typeof child === 'object' && child.type === 'img'
                  );
                  if (allImages && childArray.length > 1) {
                    return (
                      <div className="flex flex-wrap gap-2 mt-2 mb-3" {...props}>
                        {children}
                      </div>
                    );
                  }
                  return <p className="mb-2 leading-relaxed" {...props}>{children}</p>;
                },

                a: ({ node, ...props }) => <a className="text-blue-500 hover:text-blue-600 underline" target="_blank" rel="noopener noreferrer" {...props} />,
                code: ({ node, inline, className, children, ...props }: any) => {
                  return inline ? (
                    <code className="px-1.5 py-0.5 rounded text-xs font-mono" style={{ background: "var(--bg-input)", color: "#f472b6" }} {...props}>
                      {children}
                    </code>
                  ) : (
                    <code className="block p-3 rounded-lg overflow-x-auto text-xs font-mono mb-2" style={{ background: "var(--bg-input)" }} {...props}>
                      {children}
                    </code>
                  )
                }
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>

      {isUser && (
        <div
          className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center mb-1"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
        >
          <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>You</span>
        </div>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Empty state                                                                */
/* ────────────────────────────────────────────────────────────────────────── */
function EmptyState({ onSuggest }: { onSuggest: (q: string) => void }) {
  const suggestions = [
    { text: "Apa syarat pengajuan PBG?", icon: FileText },
    { text: "Bagaimana cek status permohonan?", icon: Search },
    { text: "Berapa lama proses PBG?", icon: Clock },
    { text: "Dokumen apa yang dibutuhkan?", icon: FolderOpen },
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-5 px-6 text-center relative">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-72 h-72 bg-purple-500/10 rounded-full blur-[80px] pointer-events-none" />
      <div className="flex items-center justify-center relative z-10">
        <img src="/logo-pemkot.png" alt="Pemkot Logo" className="h-24 w-auto object-contain drop-shadow-xl" />
      </div>
      <div className="relative z-10">
        <h2 className="text-3xl font-extrabold mb-2 bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-500 drop-shadow-sm">
          PBG Assist siap membantu
        </h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", maxWidth: "380px" }}>
          Tanyakan apa saja mengenai proses pengajuan Persetujuan Bangunan
          Gedung, persyaratan dokumen, atau status permohonan Anda.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4 w-full max-w-xl relative z-10">
        {suggestions.map(({ text, icon: Icon }) => (
          <button
            key={text}
            onClick={() => onSuggest(text)}
            className="flex items-center gap-3 text-left px-4 py-3.5 rounded-2xl text-sm transition-all duration-300 hover:-translate-y-1 backdrop-blur-md bg-white/5 border border-white/10 hover:border-purple-400/50 hover:bg-white/10 hover:shadow-[0_0_15px_rgba(192,132,252,0.15)] group"
            style={{ color: "var(--text-secondary)" }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--text-primary)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
            }}
          >
            <div className="flex-shrink-0 p-2 rounded-xl bg-purple-500/10 text-purple-400 group-hover:bg-purple-500/20 group-hover:scale-110 transition-all">
              <Icon size={18} />
            </div>
            <span className="font-medium leading-tight">{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Backend Health Hook                                                        */
/* ────────────────────────────────────────────────────────────────────────── */
function useBackendHealth() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await fetch("/api/health", { method: "GET" });
        setOnline(res.ok);
      } catch {
        setOnline(false);
      }
    };

    checkHealth(); // initial check
    const interval = setInterval(checkHealth, HEALTH_POLL_MS);
    return () => clearInterval(interval);
  }, []);

  return online;
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Main Page                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */
export default function ChatPage() {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [inputValue, setInputValue] = useState("");
  const [voiceEnabled, setVoiceEnabled] = useState(true);

  // States for sessions and dropdown
  const [mounted, setMounted] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // States for rename feature
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  // States for delete confirmation
  const [sessionToDelete, setSessionToDelete] = useState<string | null>(null);
  const [showDeleteAllConfirm, setShowDeleteAllConfirm] = useState(false);

  const { messages, setMessages, sendMessage, isLoading } = usePBGChat();

  const [lastSpokenId, setLastSpokenId] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);
  const voiceInitializedRef = useRef(false);

  // Theme
  const { theme, setTheme } = useTheme();

  // Backend health
  const backendOnline = useBackendHealth();

  // Hydration & Initial Load
  // Always start with a fresh chat on page load/refresh.
  // Previous sessions are loaded into the sidebar history for access.
  useEffect(() => {
    const loaded = loadAllSessions();
    setSessions(loaded);
    // Always start a fresh chat — previous sessions live in history
    setActiveSessionId(crypto.randomUUID());
    setMessages([]);
    setMounted(true);
  }, [setMessages]);

  // Persist messages to localStorage on every change
  useEffect(() => {
    if (messages.length > 0 && activeSessionId) {
      const persistable = messages.filter((m) => m.content.length > 0);
      if (persistable.length > 0) {
        setSessions(prev => {
          const idx = prev.findIndex(s => s.id === activeSessionId);
          let updatedSession: ChatSession;

          if (idx >= 0) {
            updatedSession = {
              ...prev[idx],
              messages: persistable,
              lastUpdatedAt: Date.now()
            };
          } else {
            // New session initialization
            const firstUserMessage = persistable.find((m) => m.role === "user");
            let title = "Topik Baru";
            if (firstUserMessage) {
              title = firstUserMessage.content.substring(0, 30);
              if (firstUserMessage.content.length > 30) title += "...";
            }
            updatedSession = {
              id: activeSessionId,
              title,
              messages: persistable,
              lastUpdatedAt: Date.now()
            };
          }

          saveSession(updatedSession);
          
          const copy = [...prev];
          if (idx >= 0) {
            copy[idx] = updatedSession;
          } else {
            copy.push(updatedSession);
          }
          
          return copy.sort((a, b) => {
            if (a.isPinned && !b.isPinned) return -1;
            if (!a.isPinned && b.isPinned) return 1;
            return b.lastUpdatedAt - a.lastUpdatedAt;
          });
        });
      }
    }
  }, [messages, activeSessionId]);

  // Handle clicking outside dropdown to close
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleNewChat = () => {
    setActiveSessionId(crypto.randomUUID());
    setMessages([]);
    setIsDropdownOpen(false);
  };

  const handleLoadSession = (session: ChatSession) => {
    if (editingSessionId) return; // Prevent load if we are just clicking during an edit
    setActiveSessionId(session.id);
    setMessages(session.messages);
    setIsDropdownOpen(false);
    
    const lastMsg = session.messages[session.messages.length - 1];
    if (lastMsg?.role === "assistant") {
      setLastSpokenId(lastMsg.id);
    }
  };

  const togglePin = (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    
    setSessions(prev => {
      const idx = prev.findIndex(s => s.id === sessionId);
      if (idx === -1) return prev;
      
      const session = prev[idx];
      const currentlyPinned = session.isPinned;
      
      if (!currentlyPinned) {
        const pinnedCount = prev.filter(s => s.isPinned).length;
        if (pinnedCount >= 3) {
          alert("Maksimal 3 obrolan yang dapat disematkan");
          return prev;
        }
      }
      
      const updatedSession = { ...session, isPinned: !currentlyPinned };
      if (!updatedSession.isPinned) {
        updatedSession.lastUpdatedAt = Date.now();
      }
      
      const newSessions = [...prev];
      newSessions[idx] = updatedSession;
      
      newSessions.sort((a, b) => {
        if (a.isPinned && !b.isPinned) return -1;
        if (!a.isPinned && b.isPinned) return 1;
        return b.lastUpdatedAt - a.lastUpdatedAt;
      });
      
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newSessions));
      return newSessions;
    });
  };

  const startRename = (e: React.MouseEvent, session: ChatSession) => {
    e.stopPropagation();
    setEditingSessionId(session.id);
    setEditTitle(session.title);
  };

  const commitRename = (e?: React.MouseEvent | React.FormEvent, sessionId?: string) => {
    if (e) e.stopPropagation();
    const targetId = sessionId || editingSessionId;
    if (!targetId || !editTitle.trim()) {
      setEditingSessionId(null);
      return;
    }
    
    setSessions(prev => {
      const idx = prev.findIndex(s => s.id === targetId);
      if (idx === -1) return prev;
      
      const updatedSession = { ...prev[idx], title: editTitle.trim() };
      const newSessions = [...prev];
      newSessions[idx] = updatedSession;
      
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newSessions));
      return newSessions;
    });
    
    setEditingSessionId(null);
  };
  
  const cancelRename = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(null);
  };

  const deleteSession = (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setSessionToDelete(sessionId);
  };

  const confirmDeleteSession = () => {
    if (!sessionToDelete) return;
    setSessions(prev => {
      const newSessions = prev.filter(s => s.id !== sessionToDelete);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newSessions));
      return newSessions;
    });
    if (activeSessionId === sessionToDelete) {
      handleNewChat();
    }
    setSessionToDelete(null);
  };

  const cancelDeleteSession = () => {
    setSessionToDelete(null);
  };

  // Prime the speech engine for mobile browsers (must be called on user interaction)
  const initVoice = () => {
    if (!voiceInitializedRef.current && typeof window !== "undefined" && window.speechSynthesis) {
      // iOS Safari ignores empty strings and 0 volume
      const utterance = new SpeechSynthesisUtterance(" ");
      utterance.volume = 0.01;
      utterance.rate = 10; // Fast
      window.speechSynthesis.speak(utterance);

      // Also trigger voices to load
      window.speechSynthesis.getVoices();

      voiceInitializedRef.current = true;
    }
  };

  // Strips markdown symbols so TTS doesn't read them out loud
  const cleanForSpeech = (text: string): string => {
    return text
      // Remove markdown headers
      .replace(/#{1,6}\s*/g, '')
      // Remove bold/italic (* or _)
      .replace(/[*_]{1,3}/g, '')
      // Remove list bullets at start of line
      .replace(/^\s*[-+•]\s+/gm, '')
      // Remove numbered lists (e.g. "1. " or "2) ")
      .replace(/^\s*\d+[.)\s]+/gm, '')
      // Strip markdown links, keep text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      // Remove backticks
      .replace(/`+/g, '')
      // Remove horizontal rules
      .replace(/^[-*_]{3,}$/gm, '')
      // Remove blockquote markers
      .replace(/^>\s*/gm, '')
      // Remove parenthesised URLs
      .replace(/https?:\/\/\S+/g, '')
      // Collapse multiple blank lines
      .replace(/\n{3,}/g, '\n\n')
      // Collapse multiple spaces
      .replace(/  +/g, ' ')
      .trim();
  };

  // Pick the best available Indonesian (or fallback) voice
  const getBestVoice = (): SpeechSynthesisVoice | null => {
    const voices = window.speechSynthesis.getVoices();
    // Prefer id-ID voices first, then any language with "id"
    const preferred = voices.find(v => v.lang === 'id-ID') ||
      voices.find(v => v.lang.startsWith('id')) ||
      // Fallback: English female voices tend to sound most natural
      voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('samantha')) ||
      voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('female')) ||
      voices.find(v => v.lang.startsWith('en')) ||
      null;
    return preferred;
  };

  // Voice output (TTS)
  useEffect(() => {
    if (!isLoading && voiceEnabled && messages.length > 0) {
      const lastMsg = messages[messages.length - 1];
      if (lastMsg.role === "assistant" && lastMsg.content && lastMsg.id !== lastSpokenId) {
        setLastSpokenId(lastMsg.id);

        if (typeof window === "undefined" || !window.speechSynthesis) return;

        if (window.speechSynthesis.speaking) {
          window.speechSynthesis.cancel();
        }

        const speak = () => {
          const cleaned = cleanForSpeech(lastMsg.content);
          if (!cleaned) return;

          const utterance = new SpeechSynthesisUtterance(cleaned);
          utterance.lang = "id-ID";
          utterance.rate = 0.92;   // Slightly slower than default = more natural
          utterance.pitch = 1.05;  // Slightly higher pitch = warmer/friendlier
          utterance.volume = 1;

          const bestVoice = getBestVoice();
          if (bestVoice) utterance.voice = bestVoice;

          window.speechSynthesis.speak(utterance);
        };

        // Voices may not be loaded yet — wait for them
        if (window.speechSynthesis.getVoices().length > 0) {
          speak();
        } else {
          window.speechSynthesis.onvoiceschanged = () => {
            speak();
            window.speechSynthesis.onvoiceschanged = null;
          };
        }
      }
    }
  }, [isLoading, messages, voiceEnabled, lastSpokenId]);

  // Stop speaking if voice is disabled mid-speech
  useEffect(() => {
    if (!voiceEnabled && typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  }, [voiceEnabled]);

  // Speech Recognition (STT) setup
  useEffect(() => {
    if (typeof window !== "undefined") {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRecognition) {
        recognitionRef.current = new SpeechRecognition();
        recognitionRef.current.continuous = false;
        recognitionRef.current.interimResults = true;
        recognitionRef.current.lang = "id-ID";

        recognitionRef.current.onresult = (event: any) => {
          let currentTranscript = "";
          for (let i = 0; i < event.results.length; ++i) {
            currentTranscript += event.results[i][0].transcript;
          }
          if (currentTranscript) {
            setInputValue(currentTranscript);
          }
        };

        recognitionRef.current.onerror = (event: any) => {
          console.error("Speech recognition error", event.error);
          setIsListening(false);
        };

        recognitionRef.current.onend = () => {
          setIsListening(false);
        };
      }
    }
  }, []);

  const toggleListening = () => {
    initVoice();
    if (!recognitionRef.current) {
      alert("Browser Anda tidak mendukung fitur pengenalan suara.");
      return;
    }
    if (isListening) {
      recognitionRef.current.stop();
    } else {
      recognitionRef.current.start();
      setIsListening(true);
    }
  };

  /* Auto-scroll */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  /* Auto-resize textarea */
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [inputValue]);

  const submitMessage = (text?: string) => {
    initVoice();
    const msg = (text ?? inputValue).trim();
    if (!msg || isLoading) return;
    setInputValue("");
    sendMessage(msg);
  };

  const handleFormSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    submitMessage();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitMessage();
    }
  };

  return (
    <div
      className="flex flex-col h-screen overflow-hidden relative"
      style={{ background: "var(--bg-base)" }}
    >
      {/* Background container that fades out when chat starts */}
      <div 
        className={`absolute inset-0 z-0 pointer-events-none transition-opacity duration-700 ease-in-out ${
          mounted && messages.length > 0 ? "opacity-0" : "opacity-100"
        }`}
      >
        <InteractiveDotBackground />
      </div>
      
      {/* Interactive elements container (sits above background) */}
      <div className="flex flex-col h-full w-full relative z-10">
      {/* ── Sticky Top Navigation Bar ── */}
      <header
        className="sticky top-0 z-50 flex items-center justify-between px-4 sm:px-6 py-4 flex-shrink-0 backdrop-blur-lg bg-white/80 dark:bg-[#13161e]/80 shadow-sm"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        {/* Left: Logo + Title */}
        <div className="flex items-center gap-3">
          <img src="/logo-pemkot.png" alt="Pemkot Logo" className="h-9 w-auto object-contain flex-shrink-0 drop-shadow-sm" />
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
              PBG Customer Support
            </span>
          </div>

          {/* Status Badge */}
          <div
            className="hidden sm:flex items-center gap-2 ml-3 px-3 py-1.5 rounded-full text-xs"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
          >
            {backendOnline === null ? (
              <>
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ background: "var(--text-secondary)" }}
                />
                <span style={{ color: "var(--text-secondary)" }}>Checking…</span>
              </>
            ) : backendOnline ? (
              <>
                <span className="status-dot-green" />
                <span style={{ color: "var(--status-green)" }}>System Operational</span>
              </>
            ) : (
              <>
                <span className="status-dot-red" />
                <span style={{ color: "#ef4444" }}>Backend Offline</span>
              </>
            )}
          </div>
        </div>

        {/* Right: Controls */}
        <div className="flex items-center gap-2">
          {/* History Dropdown */}
          {mounted && (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 hover:scale-105"
                style={{
                  background: isDropdownOpen ? "var(--bg-input)" : "transparent",
                  border: "1px solid",
                  borderColor: isDropdownOpen ? "var(--accent)" : "transparent",
                  color: "var(--text-secondary)"
                }}
                onMouseEnter={(e) => {
                  if (!isDropdownOpen) {
                    (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isDropdownOpen) {
                    (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
                  }
                }}
              >
                <History size={14} />
                <span className="hidden sm:inline">History</span>
              </button>

              {isDropdownOpen && (
                <div
                  className="absolute right-0 mt-3 w-72 rounded-xl shadow-2xl z-50 flex flex-col"
                  style={{
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    maxHeight: "380px",
                  }}
                >
                  <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: "var(--border)" }}>
                     <p className="text-xs font-bold" style={{ color: "var(--text-primary)" }}>Chat History</p>
                     <div className="flex items-center gap-2">
                       {sessions.length > 0 && (
                         <button 
                           onClick={(e) => {
                             e.stopPropagation();
                             setShowDeleteAllConfirm(true);
                           }}
                           className="text-[10px] text-red-400 hover:text-red-500 hover:underline px-1 transition-colors"
                         >
                           Delete All
                         </button>
                       )}
                       <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}>
                          {sessions.length} Session{sessions.length !== 1 ? 's' : ''}
                       </span>
                     </div>
                  </div>
                  <div className="overflow-y-auto flex-1 p-2 space-y-1">
                    {sessions.length === 0 ? (
                      <p className="text-xs text-center py-6" style={{ color: "var(--text-secondary)" }}>Belum ada histori.</p>
                    ) : (
                      sessions.map(s => (
                        <div
                          key={s.id}
                          onClick={() => handleLoadSession(s)}
                          className={`w-full text-left px-3 py-2.5 rounded-lg text-xs transition-colors flex items-center justify-between group cursor-pointer ${
                            activeSessionId === s.id ? "" : "hover:bg-black/5 dark:hover:bg-white/5"
                          }`}
                          style={{
                            background: activeSessionId === s.id ? "var(--bg-input)" : "transparent",
                            border: activeSessionId === s.id ? "1px solid var(--border)" : "1px solid transparent",
                          }}
                        >
                          {editingSessionId === s.id ? (
                             <form 
                               className="flex-1 flex items-center gap-1 mr-2 min-w-0"
                               onSubmit={(e) => commitRename(e, s.id)}
                               onClick={(e) => e.stopPropagation()}
                             >
                               <input 
                                 type="text" 
                                 autoFocus
                                 value={editTitle} 
                                 onChange={(e) => setEditTitle(e.target.value)} 
                                 onBlur={() => commitRename(undefined, s.id)}
                                 className="flex-1 bg-transparent border-b outline-none text-xs min-w-0"
                                 style={{ color: "var(--text-primary)", borderColor: "var(--accent)" }}
                               />
                               <button type="submit" className="p-1 hover:bg-black/10 dark:hover:bg-white/10 rounded flex-shrink-0">
                                 <Check size={12} style={{ color: "var(--status-green)" }}/>
                               </button>
                               <button type="button" onMouseDown={cancelRename} className="p-1 hover:bg-black/10 dark:hover:bg-white/10 rounded flex-shrink-0">
                                 <X size={12} style={{ color: "#ef4444" }}/>
                               </button>
                             </form>
                          ) : (
                             <div className="flex-1 min-w-0 pr-2 flex flex-col gap-0.5">
                                <div className="flex items-center gap-1.5">
                                  {s.isPinned && <Pin size={10} style={{ color: "var(--accent)" }} className="flex-shrink-0" />}
                                  <span className="font-semibold truncate block" style={{ color: "var(--text-primary)" }}>
                                    {s.title}
                                  </span>
                                </div>
                                <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
                                  {new Date(s.lastUpdatedAt).toLocaleDateString("id-ID", { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                </span>
                             </div>
                          )}

                          {editingSessionId !== s.id && (
                             <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                               <button 
                                 onClick={(e) => startRename(e, s)} 
                                 className="p-1.5 rounded-md hover:bg-black/10 dark:hover:bg-white/10"
                                 title="Ubah nama"
                               >
                                 <Pencil size={12} style={{ color: "var(--text-secondary)" }}/>
                               </button>
                               <button 
                                 onClick={(e) => togglePin(e, s.id)} 
                                 className="p-1.5 rounded-md hover:bg-black/10 dark:hover:bg-white/10"
                                 title={s.isPinned ? "Lepaskan sematan" : "Sematkan obrolan"}
                               >
                                 {s.isPinned ? (
                                   <PinOff size={12} style={{ color: "var(--text-secondary)" }}/>
                                 ) : (
                                   <Pin size={12} style={{ color: "var(--text-secondary)" }}/>
                                 )}
                               </button>
                               <button 
                                 onClick={(e) => deleteSession(e, s.id)} 
                                 className="p-1.5 rounded-md hover:bg-red-500/10"
                                 title="Hapus obrolan"
                                 onMouseEnter={(e) => {
                                   (e.currentTarget.firstChild as SVGElement).style.color = "#ef4444";
                                 }}
                                 onMouseLeave={(e) => {
                                   (e.currentTarget.firstChild as SVGElement).style.color = "var(--text-secondary)";
                                 }}
                               >
                                 <Trash2 size={12} style={{ color: "var(--text-secondary)", transition: "color 0.2s" }}/>
                               </button>
                             </div>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Theme Toggle */}
          {mounted && (
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-200 hover:scale-110"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              aria-label="Toggle theme"
            >
              {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          )}

          {/* Voice Toggle */}
          <button
            onClick={() => {
              initVoice();
              setVoiceEnabled(!voiceEnabled);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 hover:scale-105"
            style={{
              background: voiceEnabled ? "rgba(129, 140, 248, 0.1)" : "var(--bg-input)",
              border: "1px solid",
              borderColor: voiceEnabled ? "var(--accent)" : "var(--border)",
              color: voiceEnabled ? "var(--accent)" : "var(--text-secondary)"
            }}
          >
            {voiceEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
            <span className="hidden sm:inline">{voiceEnabled ? "Voice On" : "Voice Off"}</span>
          </button>

          {/* New Chat */}
          <button
            onClick={handleNewChat}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 hover:scale-105"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "#f472b6";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "#f472b6";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
            }}
          >
            <Plus size={14} />
            <span className="hidden sm:inline">New Chat</span>
          </button>
        </div>
      </header>

      {/* ── Messages ── */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-4xl mx-auto space-y-5">
          {!mounted || messages.length === 0 ? (
            <EmptyState onSuggest={(q) => submitMessage(q)} />
          ) : (
            <>
              {messages.map((m) => {
                // Don't render empty assistant bubbles (TypingIndicator handles this state)
                if (m.role === "assistant" && !m.content) return null;
                return <MessageBubble key={m.id} role={m.role} content={m.content} />;
              })}
              {isLoading && messages[messages.length - 1]?.role === "assistant" && !messages[messages.length - 1]?.content && (
                <TypingIndicator />
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>
      </div>

      {/* ── Input ── */}
      <div className="px-4 py-6 mb-4 flex-shrink-0 relative z-20">
        <div className="max-w-4xl mx-auto">
          <form
            onSubmit={handleFormSubmit}
            className="flex items-end gap-3 rounded-2xl px-4 py-3 shadow-xl transition-all duration-300 focus-within:ring-2 focus-within:ring-purple-500/50 focus-within:border-purple-500/50 backdrop-blur-md"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
          >
            <textarea
              id="chat-input"
              ref={textareaRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Tanyakan tentang PBG… (Enter untuk kirim, Shift+Enter untuk baris baru)"
              rows={1}
              disabled={isLoading}
              className="flex-1 resize-none bg-transparent outline-none text-sm leading-relaxed p-0 m-0 py-1.5"
              style={{ color: "var(--text-primary)", maxHeight: "160px", overflowY: "auto" }}
            />
            <button
              type="button"
              onClick={toggleListening}
              className={`flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200 mb-0.5 ${isListening ? "animate-pulse" : ""}`}
              style={{
                background: isListening ? "rgba(244, 114, 182, 0.1)" : "var(--bg-input)",
                border: "1px solid",
                borderColor: isListening ? "#f472b6" : "var(--border)",
                color: isListening ? "#f472b6" : "var(--text-secondary)",
              }}
            >
              <Mic size={15} />
            </button>
            <button
              id="send-button"
              type="submit"
              disabled={isLoading || !inputValue.trim()}
              className="flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200 mb-0.5"
              style={{
                background:
                  isLoading || !inputValue.trim()
                    ? "var(--bg-input)"
                    : "linear-gradient(135deg,#818cf8,#c084fc)",
                cursor: isLoading || !inputValue.trim() ? "not-allowed" : "pointer",
                border: isLoading || !inputValue.trim() ? "1px solid var(--border)" : "none",
              }}
            >
              <Send
                size={15}
                color={isLoading || !inputValue.trim() ? "var(--text-secondary)" : "#fff"}
              />
            </button>
          </form>
          <p className="text-center mt-2" style={{ fontSize: "0.65rem", color: "var(--text-secondary)" }}>
            PBG Assist dapat membuat kesalahan. Selalu verifikasi informasi penting dengan instansi terkait.
          </p>
        </div>
      </div>
      {/* Delete Confirmation Modal */}
      {sessionToDelete && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div 
            className="w-full max-w-sm rounded-2xl p-6 shadow-2xl animate-in fade-in zoom-in-95 duration-200"
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
          >
            <h3 className="text-lg font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Hapus Obrolan</h3>
            <p className="text-sm mb-6" style={{ color: "var(--text-secondary)" }}>
              Apakah Anda yakin ingin menghapus obrolan ini? Tindakan ini tidak dapat dibatalkan.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button
                onClick={cancelDeleteSession}
                className="px-4 py-2 text-sm rounded-xl transition-colors hover:bg-black/5 dark:hover:bg-white/5"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                Batal
              </button>
              <button
                onClick={confirmDeleteSession}
                className="px-4 py-2 text-sm rounded-xl text-white transition-colors"
                style={{ background: "#ef4444", border: "1px solid #ef4444" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = "#dc2626";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = "#ef4444";
                }}
              >
                Hapus
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete All Confirmation Modal */}
      {showDeleteAllConfirm && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div 
            className="w-full max-w-sm rounded-2xl p-6 shadow-2xl animate-in fade-in zoom-in-95 duration-200"
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
          >
            <h3 className="text-lg font-semibold mb-2" style={{ color: "var(--text-primary)" }}>Hapus Semua Obrolan</h3>
            <p className="text-sm mb-6" style={{ color: "var(--text-secondary)" }}>
              Apakah Anda yakin ingin menghapus <strong>seluruh</strong> riwayat obrolan? Tindakan ini tidak dapat dibatalkan.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button
                onClick={() => setShowDeleteAllConfirm(false)}
                className="px-4 py-2 text-sm rounded-xl transition-colors hover:bg-black/5 dark:hover:bg-white/5"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                Batal
              </button>
              <button
                onClick={() => {
                  localStorage.removeItem("pbg-chat-sessions");
                  setSessions([]);
                  setShowDeleteAllConfirm(false);
                  setIsDropdownOpen(false);
                }}
                className="px-4 py-2 text-sm rounded-xl text-white transition-colors"
                style={{ background: "#ef4444", border: "1px solid #ef4444" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = "#dc2626";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = "#ef4444";
                }}
              >
                Ya, Hapus Semua
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
