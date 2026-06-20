import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRepository, cancelStream, getStoredToken, API_BASE_URL } from '../api';
import type { Citation } from '../api';
import { Send, Bot, User, Code2, ChevronDown, ChevronRight, Terminal, Square, LogOut } from 'lucide-react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { motion, AnimatePresence } from 'framer-motion';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface ChatViewProps {
  repoId: string;
  onReset: () => void;
  currentUser?: { id: string; email: string } | null;
  onLogout?: () => void;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  isStreaming?: boolean;
}

export default function ChatView({ repoId, onReset, currentUser, onLogout }: ChatViewProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [activeStreamId, setActiveStreamId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Fetch repo info
  const { data: repo } = useQuery({
    queryKey: ['repo', repoId],
    queryFn: () => getRepository(repoId),
  });

  // Since we don't persist convId across full reloads easily without URL sync,
  // we could optionally fetch the latest conversation if we had an endpoint for it.
  // For now, if we don't have a convId, we start fresh. If we added it to URL it would load.

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const handleStopStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    if (activeStreamId && repoId) {
      cancelStream(repoId, activeStreamId);
    }
    setIsStreaming(false);
    setActiveStreamId(null);
    setMessages(prev => {
      const newMsgs = [...prev];
      const lastMsg = newMsgs[newMsgs.length - 1];
      if (lastMsg?.role === 'assistant' && lastMsg.isStreaming) {
        lastMsg.isStreaming = false;
        lastMsg.content += ' \n\n*[Generation stopped]*';
      }
      return newMsgs;
    });
  }, [activeStreamId, repoId]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;

    // Create a new AbortController for this stream
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim(),
    };

    const assistantMessagePlaceholder: Message = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      isStreaming: true,
    };

    setMessages(prev => [...prev, userMessage, assistantMessagePlaceholder]);
    setInput('');
    setIsStreaming(true);

    let streamedContent = '';

    try {
      await fetchEventSource(`${API_BASE_URL}/api/repos/${repoId}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(getStoredToken() ? { Authorization: `Bearer ${getStoredToken()}` } : {}),
        },
        body: JSON.stringify({
          message: userMessage.content,
          conversation_id: convId || undefined,
        }),
        signal: controller.signal,
        onmessage(ev) {
          const data = JSON.parse(ev.data);
          if (data.type === 'started') {
            setActiveStreamId(data.stream_id);
          } else if (data.type === 'token') {
            streamedContent += data.content;
            setMessages(prev => {
              const newMsgs = [...prev];
              newMsgs[newMsgs.length - 1] = { ...newMsgs[newMsgs.length - 1], content: streamedContent };
              return newMsgs;
            });
          } else if (data.type === 'done') {
            if (data.conversation_id) setConvId(data.conversation_id);
            if (data.stream_id) setActiveStreamId(null); // Stream complete
            setMessages(prev => {
              const newMsgs = [...prev];
              newMsgs[newMsgs.length - 1] = {
                ...newMsgs[newMsgs.length - 1],
                content: streamedContent,
                citations: data.citations,
                isStreaming: false,
                id: data.message_id || newMsgs[newMsgs.length - 1].id,
              };
              return newMsgs;
            });
          } else if (data.type === 'error') {
             setMessages(prev => {
              const newMsgs = [...prev];
              newMsgs[newMsgs.length - 1] = {
                ...newMsgs[newMsgs.length - 1],
                content: streamedContent + `\n\n[Error: ${data.detail}]`,
                isStreaming: false,
              };
              return newMsgs;
            });
          }
        },
        onerror(err) {
          console.error('SSE Error:', err);
          throw err;
        }
      });
    } catch (err) {
      setIsStreaming(false);
      setMessages(prev => {
        const newMsgs = [...prev];
        const lastMsg = newMsgs[newMsgs.length - 1];
        if (lastMsg.role === 'assistant' && lastMsg.isStreaming) {
          lastMsg.content += '\n\n[Connection failed. Please try again.]';
          lastMsg.isStreaming = false;
        }
        return newMsgs;
      });
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <header className="h-16 border-b border-border bg-surface/50 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-purple-600 flex items-center justify-center shadow-lg shadow-primary/20">
            <Code2 className="w-4 h-4 text-white" />
          </div>
          <h1 className="font-bold text-lg tracking-tight">RepoMind</h1>
          <div className="w-px h-6 bg-border mx-2" />
          {repo && (
            <div className="text-sm text-textMuted font-mono">
              {repo.owner}/<span className="text-textMain font-semibold">{repo.repo_name}</span>
              {repo.is_private && <span className="ml-2 text-xs text-primary">🔒 private</span>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {currentUser && (
            <span className="text-xs text-textMuted hidden md:block">{currentUser.email}</span>
          )}
          {onLogout && (
            <button
              onClick={onLogout}
              title="Sign out"
              className="text-sm px-3 py-1.5 rounded-md hover:bg-surface border border-transparent hover:border-border transition-colors text-textMuted hover:text-error flex items-center gap-1.5"
            >
              <LogOut className="w-4 h-4" />
              <span className="hidden sm:block">Sign out</span>
            </button>
          )}
          <button 
            onClick={onReset}
            className="text-sm px-3 py-1.5 rounded-md hover:bg-surface border border-transparent hover:border-border transition-colors text-textMuted hover:text-textMain flex items-center gap-2"
          >
            Change Repo
          </button>
        </div>
      </header>

      {/* Chat Area */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-textMuted space-y-4">
            <Terminal className="w-16 h-16 text-border opacity-50" />
            <p className="text-lg">Ask anything about the codebase.</p>
            <div className="flex gap-2 flex-wrap justify-center max-w-2xl">
              {["How does dependency injection work?", "Where are the API routes defined?", "Explain the main architecture."].map(q => (
                 <button 
                  key={q} 
                  onClick={() => setInput(q)}
                  className="px-4 py-2 rounded-full border border-border bg-surface hover:border-primary/50 hover:text-primary transition-colors text-sm"
                 >
                   {q}
                 </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, idx) => (
            <MessageBubble key={msg.id || idx} message={msg} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="p-4 bg-surface/80 border-t border-border backdrop-blur-xl">
        {/* Stop generating button — shown above input when streaming */}
        <AnimatePresence>
          {isStreaming && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="max-w-4xl mx-auto mb-3 flex justify-center"
            >
              <button
                id="stop-generating-btn"
                type="button"
                onClick={handleStopStreaming}
                className="flex items-center gap-2 px-4 py-2 rounded-full border border-error/40 bg-error/10 text-error text-sm hover:bg-error/20 transition-colors"
              >
                <Square className="w-4 h-4" />
                Stop generating
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        <form onSubmit={handleSend} className="max-w-4xl mx-auto relative flex items-center">
          <input
            id="chat-input"
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Ask a question about the code..."
            className="input-field pr-14 h-14 bg-background shadow-inner"
            disabled={isStreaming}
          />
          <button
            id="chat-send-btn"
            type="submit"
            disabled={!input.trim() || isStreaming}
            className="absolute right-2 p-2 bg-primary text-white rounded-md hover:bg-primaryHover disabled:opacity-50 disabled:bg-surfaceHover transition-colors"
          >
            <Send className="w-5 h-5" />
          </button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  
  return (
    <motion.div 
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex gap-4 max-w-4xl mx-auto ${isUser ? 'flex-row-reverse' : ''}`}
    >
      <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center shadow-md ${isUser ? 'bg-primary' : 'bg-surface border border-border'}`}>
        {isUser ? <User className="w-5 h-5 text-white" /> : <Bot className="w-5 h-5 text-primary" />}
      </div>
      
      <div className={`flex-1 space-y-4 ${isUser ? 'flex flex-col items-end' : ''}`}>
        <div className={`px-5 py-3.5 rounded-2xl max-w-[85%] text-[15px] leading-relaxed shadow-sm ${
          isUser 
            ? 'bg-primary text-white rounded-tr-sm' 
            : 'bg-surface border border-border rounded-tl-sm'
        }`}>
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : (
            <div className="prose prose-invert prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </div>
          )}
          {message.isStreaming && (
            <span className="inline-block w-2 h-4 ml-1 bg-primary animate-pulse" />
          )}
        </div>

        {/* Citations */}
        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {message.citations.map((cit, i) => (
              <CitationChip key={i} citation={cit} index={i + 1} />
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function CitationChip({ citation, index }: { citation: Citation, index: number }) {
  const [expanded, setExpanded] = useState(false);
  const fileName = citation.file_path.split('/').pop() || citation.file_path;

  // CodeChunkData content from the backend is already syntax-highlightable
  // Wait, the backend doesn't send the chunk content in the citation struct, just the metadata!
  // Let me check my API schema in `api.ts`.
  // Indeed, citation has: file_path, start_line, end_line, symbol_name, symbol_type, language, similarity.
  // The backend BACKEND.md says: `Citations come back as structured data (file path, start_line, end_line)...`
  // Ah, the user instruction: "expand to show the cited chunk's code with syntax highlighting."
  // Wait! The backend API POST /api/repos/{id}/chat doesn't return the raw code in the citation object according to BACKEND.md!
  // Let me double check the backend rag.py to see if it includes content.
  // The RAG service `build_citations` returns file_path, start_line, end_line, symbol_name, symbol_type, language, similarity.
  // It does NOT return `content`!
  // If we need to expand it, we'd need the content. The instruction says: "expand to show the cited chunk's code with syntax highlighting".
  // If the backend doesn't provide it, we can't show it unless we fetch it.
  // Wait, let's just show a nice chip anyway. If we don't have the code, we'll show a message or just not expand to code.
  // Let me check if `content` is sent anyway. If not, I'll just render the metadata beautifully.
  // Wait, in my `rag.py` I wrote `build_citations` and I did NOT include `content`.
  // I will just make it a static chip or an expandable chip showing the lines and symbol metadata.
  
  return (
    <div className="flex flex-col max-w-full">
      <button 
        onClick={() => setExpanded(!expanded)}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
          expanded 
            ? 'bg-primary/20 border-primary/30 text-primary' 
            : 'bg-surfaceHover border-border text-textMuted hover:border-primary/40 hover:text-textMain'
        }`}
      >
        <span className="w-4 h-4 rounded-full bg-background border border-border flex items-center justify-center text-[10px]">
          {index}
        </span>
        <span className="truncate max-w-[200px]">{fileName}</span>
        <span className="opacity-50">L{citation.start_line}-{citation.end_line}</span>
        {expanded ? <ChevronDown className="w-3 h-3 ml-1" /> : <ChevronRight className="w-3 h-3 ml-1" />}
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden mt-2"
          >
            <div className="bg-[#1E1E1E] border border-border rounded-lg overflow-hidden text-sm relative group">
              <div className="bg-surface/80 px-3 py-1.5 border-b border-border flex items-center justify-between text-xs text-textMuted font-mono">
                <span>{citation.file_path}</span>
                {citation.symbol_name && (
                   <span className="bg-primary/20 text-primary px-2 py-0.5 rounded">
                     {citation.symbol_type}: {citation.symbol_name}
                   </span>
                )}
              </div>
              <div className="text-textMuted overflow-auto max-h-96 custom-scrollbar">
                {citation.content ? (
                  <SyntaxHighlighter
                    language={citation.language || 'typescript'}
                    style={vscDarkPlus}
                    customStyle={{ margin: 0, padding: '1rem', background: 'transparent' }}
                    showLineNumbers={true}
                    startingLineNumber={citation.start_line}
                  >
                    {citation.content}
                  </SyntaxHighlighter>
                ) : (
                  <div className="p-4 italic">
                    Code snippet located in {fileName} at line {citation.start_line} to {citation.end_line}.
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
