import { useState } from 'react';
import type { FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { submitRepository } from '../api';
import { GitBranch, ArrowRight, Code2, Lock, Key, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface LandingViewProps {
  onRepoSubmitted: (repoId: string) => void;
}

export default function LandingView({ onRepoSubmitted }: LandingViewProps) {
  const [url, setUrl] = useState('');
  const [githubToken, setGithubToken] = useState('');
  const [isPrivate, setIsPrivate] = useState(false);
  const [validationError, setValidationError] = useState('');

  const submitMutation = useMutation({
    mutationFn: submitRepository,
    onSuccess: (data) => {
      onRepoSubmitted(data.id);
    },
    onError: (error: Error) => {
      setValidationError(error.message);
    }
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setValidationError('');
    
    const trimmed = url.trim();
    if (!trimmed.startsWith('https://github.com/')) {
      setValidationError('Please enter a valid GitHub URL starting with https://github.com/');
      return;
    }

    if (isPrivate && !githubToken.trim()) {
      setValidationError('Please enter a GitHub Personal Access Token for private repositories.');
      return;
    }
    
    submitMutation.mutate({
      github_url: trimmed,
      github_token: isPrivate ? githubToken.trim() : undefined,
    });
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden">
      {/* Background blobs */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/20 rounded-full blur-3xl mix-blend-screen opacity-50" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl mix-blend-screen opacity-50" />
      
      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        className="glass-panel max-w-xl w-full p-8 md:p-12 rounded-2xl relative z-10"
      >
        <div className="flex justify-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary to-purple-600 flex items-center justify-center shadow-lg shadow-primary/30">
            <Code2 className="w-8 h-8 text-white" />
          </div>
        </div>

        <h1 className="text-4xl md:text-5xl font-bold text-center mb-4 tracking-tight">
          Repo<span className="text-primary">Mind</span>
        </h1>
        <p className="text-textMuted text-center text-lg mb-10">
          Ask natural-language questions about any codebase. Grounded in actual code.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* URL input */}
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
              <GitBranch className="h-5 w-5 text-textMuted group-focus-within:text-primary transition-colors" />
            </div>
            <input
              id="github-url-input"
              type="text"
              placeholder="https://github.com/owner/repo"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="input-field pl-12 h-14 text-lg bg-surface/50 backdrop-blur-sm"
              disabled={submitMutation.isPending}
            />
          </div>

          {/* Private repo toggle */}
          <button
            type="button"
            onClick={() => setIsPrivate(!isPrivate)}
            className="flex items-center gap-2 text-sm text-textMuted hover:text-textPrimary transition-colors w-full px-1"
          >
            <Lock className="w-4 h-4" />
            <span>Private repository?</span>
            {isPrivate ? <ChevronUp className="w-4 h-4 ml-auto" /> : <ChevronDown className="w-4 h-4 ml-auto" />}
          </button>

          {/* PAT input (animated) */}
          <AnimatePresence>
            {isPrivate && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                className="overflow-hidden"
              >
                <div className="space-y-2">
                  <div className="relative group">
                    <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                      <Key className="h-5 w-5 text-textMuted group-focus-within:text-primary transition-colors" />
                    </div>
                    <input
                      id="github-token-input"
                      type="password"
                      placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                      value={githubToken}
                      onChange={(e) => setGithubToken(e.target.value)}
                      className="input-field pl-12 h-12 text-sm bg-surface/50 backdrop-blur-sm"
                      disabled={submitMutation.isPending}
                    />
                  </div>
                  <p className="text-xs text-textMuted px-1">
                    Your token is{' '}
                    <strong className="text-textPrimary">never stored</strong>{' '}
                    — used only during cloning.{' '}
                    <a
                      href="https://github.com/settings/tokens/new?scopes=repo&description=RepoMind+Read-Only"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline inline-flex items-center gap-1"
                    >
                      Create a read-only token
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {validationError && (
            <motion.p 
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              className="text-error text-sm px-2"
            >
              {validationError}
            </motion.p>
          )}

          <button
            id="index-repo-btn"
            type="submit"
            disabled={!url || submitMutation.isPending}
            className="w-full h-14 btn-primary flex items-center justify-center gap-2 text-lg mt-4"
          >
            {submitMutation.isPending ? (
              <span className="flex items-center gap-2">
                <svg className="animate-spin -ml-1 mr-2 h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Initializing...
              </span>
            ) : (
              <>
                {isPrivate ? <Lock className="w-5 h-5" /> : <ArrowRight className="w-5 h-5" />}
                {isPrivate ? 'Index Private Repository' : 'Index Repository'}
              </>
            )}
          </button>
        </form>
      </motion.div>
    </div>
  );
}
