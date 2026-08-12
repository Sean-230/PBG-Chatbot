"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Building2,
  ChevronLeft,
  ChevronRight,
  Send,
  Database,
  Cpu,
  MessageSquare,
  RotateCcw,
  Mic,
  Volume2,
  VolumeX,
} from "lucide-react";

/* ────────────────────────────────────────────────────────────────────────── */
/*  Types                                                                      */
/* ────────────────────────────────────────────────────────────────────────── */
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

/* ────────────────────────────────────────────────────────────────────────── */
/*  Custom chat hook — calls FastAPI backend directly via fetch + streaming    */
/* ────────────────────────────────────────────────────────────────────────── */
function usePBGChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  messagesRef.current = messages;

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

  const clearMessages = useCallback(() => setMessages([]), []);

  return { messages, sendMessage, isLoading, clearMessages };
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
        style={{ background: "#1a1d27", border: "1px solid #1f2330" }}
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
        className={`max-w-[72%] px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "rounded-2xl rounded-br-sm text-white"
            : "rounded-2xl rounded-tl-sm"
        }`}
        style={
          isUser
            ? { background: "linear-gradient(135deg, #4338ca 0%, #6d28d9 100%)" }
            : { background: "#1a1d27", border: "1px solid #1f2330", color: "#d1d5e8" }
        }
      >
        {isUser ? (
          <p className="whitespace-pre-wrap m-0">{content}</p>
        ) : (
          <div className="markdown-content text-sm">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({node, ...props}) => <p className="mb-2 last:mb-0 leading-relaxed" {...props} />,
                ul: ({node, ...props}) => <ul className="list-disc pl-5 mb-2 space-y-1" {...props} />,
                ol: ({node, ...props}) => <ol className="list-decimal pl-5 mb-2 space-y-1" {...props} />,
                li: ({node, ...props}) => <li {...props} />,
                h1: ({node, ...props}) => <h1 className="text-xl font-bold mb-2 mt-4 text-white" {...props} />,
                h2: ({node, ...props}) => <h2 className="text-lg font-bold mb-2 mt-4 text-white" {...props} />,
                h3: ({node, ...props}) => <h3 className="text-md font-bold mb-2 mt-3 text-white" {...props} />,
                strong: ({node, ...props}) => <strong className="font-semibold text-[#f8fafc]" {...props} />,
                a: ({node, ...props}) => <a className="text-indigo-400 hover:underline" {...props} />,
                code: ({node, inline, className, children, ...props}: any) => {
                  const match = /language-(\w+)/.exec(className || '')
                  return inline ? (
                    <code className="bg-[#1f2330] px-1.5 py-0.5 rounded text-[#f472b6] text-xs font-mono" {...props}>
                      {children}
                    </code>
                  ) : (
                    <code className="block bg-[#1f2330] p-3 rounded-lg overflow-x-auto text-xs font-mono mb-2" {...props}>
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
          style={{ background: "#1f2330", border: "1px solid #2a2f42" }}
        >
          <span style={{ fontSize: "0.7rem", color: "#8b92a8" }}>You</span>
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
    "Apa syarat pengajuan PBG?",
    "Bagaimana cek status permohonan?",
    "Berapa lama proses PBG?",
    "Dokumen apa yang dibutuhkan?",
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-5 px-6 text-center">
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center"
        style={{ background: "linear-gradient(135deg,#818cf8,#c084fc)" }}
      >
        <Building2 size={32} color="#fff" />
      </div>
      <div>
        <h2 className="text-xl font-semibold mb-1.5 gradient-text">
          PBG Assist siap membantu
        </h2>
        <p style={{ color: "#8b92a8", fontSize: "0.875rem", maxWidth: "380px" }}>
          Tanyakan apa saja mengenai proses pengajuan Persetujuan Bangunan
          Gedung, persyaratan dokumen, atau status permohonan Anda.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2 w-full max-w-md">
        {suggestions.map((q) => (
          <button
            key={q}
            onClick={() => onSuggest(q)}
            className="text-left px-3.5 py-2.5 rounded-xl text-xs transition-all duration-200 hover:scale-[1.02]"
            style={{
              background: "#13161e",
              border: "1px solid #1f2330",
              color: "#8b92a8",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = "#818cf8";
              (e.currentTarget as HTMLButtonElement).style.color = "#c7d0f0";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = "#1f2330";
              (e.currentTarget as HTMLButtonElement).style.color = "#8b92a8";
            }}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Main Page                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */
export default function ChatPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [inputValue, setInputValue] = useState("");
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [lastSpokenId, setLastSpokenId] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);
  const voiceInitializedRef = useRef(false);

  const { messages, sendMessage, isLoading, clearMessages } = usePBGChat();

  // Prime the speech engine for mobile browsers (must be called on user interaction)
  const initVoice = () => {
    if (!voiceInitializedRef.current && typeof window !== "undefined" && window.speechSynthesis) {
      const utterance = new SpeechSynthesisUtterance("");
      utterance.volume = 0; // Silent
      window.speechSynthesis.speak(utterance);
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

        window.speechSynthesis.cancel();

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

  useEffect(() => {
    if (typeof window !== "undefined" && window.innerWidth >= 768) {
      setSidebarOpen(true);
    }
  }, []);

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
      className="flex h-screen overflow-hidden relative"
      style={{ background: "var(--bg-base)" }}
    >
      {/* ── Sidebar Overlay (Mobile) ── */}
      {sidebarOpen && (
        <div 
          className="md:hidden absolute inset-0 z-10 bg-black/50 transition-opacity" 
          onClick={() => setSidebarOpen(false)}
        />
      )}
      
      {/* ── Sidebar ── */}
      <aside
        className="sidebar-transition flex flex-col overflow-hidden absolute md:relative z-20 h-full"
        style={{
          width: sidebarOpen ? "260px" : "0px",
          minWidth: sidebarOpen ? "260px" : "0px",
          opacity: sidebarOpen ? 1 : 0,
          background: "var(--bg-sidebar)",
          borderRight: "1px solid var(--border)",
        }}
      >
        <div
          className="flex flex-col h-full p-5 overflow-hidden"
          style={{ minWidth: "260px" }}
        >
          {/* Logo */}
          <div className="flex items-center gap-3 mb-6">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
              style={{ background: "linear-gradient(135deg,#818cf8,#c084fc)" }}
            >
              <Building2 size={20} color="#fff" />
            </div>
            <div>
              <h1 className="text-sm font-bold gradient-text leading-tight">
                PBG Assist
              </h1>
              <p style={{ color: "#6b7280", fontSize: "0.7rem" }}>
                Asisten Layanan Bangunan
              </p>
            </div>
          </div>

          <div className="mb-5" style={{ height: "1px", background: "var(--border)" }} />

          {/* Status */}
          <div className="mb-5">
            <p className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "#4b5563" }}>
              Sistem Status
            </p>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2.5">
                <span className="status-dot" />
                <div className="flex items-center gap-1.5">
                  <Database size={12} style={{ color: "#6b7280" }} />
                  <span style={{ color: "#8b92a8", fontSize: "0.78rem" }}>Knowledge Base: Online</span>
                </div>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="status-dot" />
                <div className="flex items-center gap-1.5">
                  <Cpu size={12} style={{ color: "#6b7280" }} />
                  <span style={{ color: "#8b92a8", fontSize: "0.78rem" }}>Python AI Engine: Ready</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mb-5" style={{ height: "1px", background: "var(--border)" }} />

          {/* About */}
          <div className="flex-1">
            <p className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "#4b5563" }}>
              Tentang
            </p>
            <p style={{ color: "#6b7280", fontSize: "0.75rem", lineHeight: "1.6" }}>
              PBG Assist adalah asisten AI untuk membantu masyarakat memahami
              prosedur Persetujuan Bangunan Gedung berdasarkan regulasi terbaru.
            </p>
          </div>

          {/* Footer */}
          <div className="pt-4" style={{ borderTop: "1px solid var(--border)" }}>
            <p style={{ color: "#374151", fontSize: "0.68rem", textAlign: "center" }}>
              v0.2.0 · PBG Assist
            </p>
          </div>
        </div>
      </aside>

      {/* ── Main ── */}
      <main className="flex flex-col flex-1 overflow-hidden">
        {/* Header */}
        <header
          className="flex items-center justify-between px-5 py-3.5 flex-shrink-0"
          style={{ background: "var(--bg-surface)", borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen((p) => !p)}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-200 hover:scale-110"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "#8b92a8" }}
              aria-label="Toggle sidebar"
            >
              {sidebarOpen ? <ChevronLeft size={15} /> : <ChevronRight size={15} />}
            </button>
            <div className="flex items-center gap-2">
              <MessageSquare size={16} style={{ color: "#818cf8" }} />
              <span className="font-semibold text-sm" style={{ color: "#c7d0f0" }}>
                PBG Customer Support
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                initVoice();
                setVoiceEnabled(!voiceEnabled);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 hover:scale-105"
              style={{
                background: voiceEnabled ? "rgba(129, 140, 248, 0.1)" : "var(--bg-input)",
                border: "1px solid",
                borderColor: voiceEnabled ? "#818cf8" : "var(--border)",
                color: voiceEnabled ? "#818cf8" : "#6b7280"
              }}
            >
              {voiceEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
              {voiceEnabled ? "Voice On" : "Voice Off"}
            </button>
            {messages.length > 0 && (
              <button
                onClick={clearMessages}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 hover:scale-105"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "#6b7280" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.color = "#f472b6";
                  (e.currentTarget as HTMLButtonElement).style.borderColor = "#f472b6";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.color = "#6b7280";
                  (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
                }}
              >
                <RotateCcw size={12} />
                Clear chat
              </button>
            )}
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6 space-y-5">
          {messages.length === 0 ? (
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

        {/* Input */}
        <div
          className="px-4 py-4 flex-shrink-0"
          style={{ background: "var(--bg-surface)", borderTop: "1px solid var(--border)" }}
        >
          <form
            onSubmit={handleFormSubmit}
            className="flex items-end gap-3 rounded-2xl px-4 py-3 input-glow transition-all duration-200"
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
              style={{ color: "#d1d5e8", maxHeight: "160px", overflowY: "auto" }}
            />
            <button
              type="button"
              onClick={toggleListening}
              className={`flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200 mb-0.5 ${isListening ? "animate-pulse" : ""}`}
              style={{
                background: isListening ? "rgba(244, 114, 182, 0.1)" : "#1f2330",
                border: "1px solid",
                borderColor: isListening ? "#f472b6" : "transparent",
                color: isListening ? "#f472b6" : "#6b7280",
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
                    ? "#1f2330"
                    : "linear-gradient(135deg,#818cf8,#c084fc)",
                cursor: isLoading || !inputValue.trim() ? "not-allowed" : "pointer",
              }}
            >
              <Send
                size={15}
                color={isLoading || !inputValue.trim() ? "#374151" : "#fff"}
              />
            </button>
          </form>
          <p className="text-center mt-2" style={{ fontSize: "0.65rem", color: "#374151" }}>
            PBG Assist dapat membuat kesalahan. Selalu verifikasi informasi penting dengan instansi terkait.
          </p>
        </div>
      </main>
    </div>
  );
}
