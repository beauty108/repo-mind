import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRepository } from '../api';
import { Loader2, CheckCircle2, XCircle, FileCode2, GitCommit } from 'lucide-react';
import { motion } from 'framer-motion';

interface IndexingViewProps {
  repoId: string;
  onReady: () => void;
  onReset: () => void;
}

export default function IndexingView({ repoId, onReady, onReset }: IndexingViewProps) {
  const { data: repo, error } = useQuery({
    queryKey: ['repo', repoId],
    queryFn: () => getRepository(repoId),
    refetchInterval: (query) => {
      // Poll every 3 seconds if status is pending or indexing
      const status = query.state.data?.status;
      if (status === 'pending' || status === 'indexing') return 3000;
      return false;
    },
  });

  useEffect(() => {
    if (repo?.status === 'ready') {
      // Small delay for smooth transition
      const timer = setTimeout(() => onReady(), 1500);
      return () => clearTimeout(timer);
    }
  }, [repo?.status, onReady]);

  const isError = !!error || repo?.status === 'failed';
  const errorMessage = error?.message || repo?.error_message || 'An unknown error occurred during indexing.';

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="glass-panel max-w-md w-full p-8 rounded-2xl text-center"
      >
        <div className="flex justify-center mb-6">
          {isError ? (
            <XCircle className="w-16 h-16 text-error" />
          ) : repo?.status === 'ready' ? (
            <CheckCircle2 className="w-16 h-16 text-success" />
          ) : (
            <div className="relative">
              <Loader2 className="w-16 h-16 text-primary animate-spin" />
              <div className="absolute inset-0 bg-primary/20 rounded-full blur-xl animate-pulse-slow" />
            </div>
          )}
        </div>

        <h2 className="text-2xl font-bold mb-2">
          {isError ? 'Indexing Failed' : 
           repo?.status === 'ready' ? 'Indexing Complete' : 
           repo?.status === 'indexing' ? 'Parsing Codebase...' : 
           'Cloning Repository...'}
        </h2>
        
        {repo && !isError && (
          <p className="text-textMuted mb-6 font-mono text-sm break-all">
            {repo.owner}/{repo.repo_name}
          </p>
        )}

        {isError && (
          <div className="bg-error/10 border border-error/20 p-4 rounded-lg mb-6">
            <p className="text-error text-sm">{errorMessage}</p>
          </div>
        )}

        {!isError && repo && (repo.status === 'indexing' || repo.status === 'ready') && (
          <div className="space-y-4 mb-6">
            <div className="bg-surface/50 p-4 rounded-xl flex items-center justify-between border border-border">
              <div className="flex items-center gap-3 text-textMuted">
                <FileCode2 className="w-5 h-5 text-primary" />
                <span>Files Indexed</span>
              </div>
              <span className="font-bold text-xl">{repo.indexed_file_count}</span>
            </div>
            
            {repo.skipped_file_count > 0 && (
              <p className="text-sm text-textMuted bg-surfaceHover py-2 px-3 rounded text-left flex items-start gap-2">
                <span className="text-amber-500 mt-0.5">⚠️</span> 
                Skipped {repo.skipped_file_count} files in unsupported languages or above size limit.
              </p>
            )}

            {repo.indexed_commit_sha && (
              <div className="flex items-center gap-2 text-sm text-textMuted justify-center">
                <GitCommit className="w-4 h-4" />
                <span className="font-mono">{repo.indexed_commit_sha.substring(0, 7)}</span>
              </div>
            )}
          </div>
        )}

        {isError && (
          <button onClick={onReset} className="btn-primary w-full h-12">
            Try Another Repository
          </button>
        )}
      </motion.div>
    </div>
  );
}
