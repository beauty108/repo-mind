import { useState } from 'react';
import type { FormEvent } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useMutation } from '@tanstack/react-query';
import { Code2, Mail, Lock, UserPlus, LogIn } from 'lucide-react';
import { login, register, setStoredToken } from '../api';
import type { TokenResponse } from '../api';

interface AuthViewProps {
  onAuthenticated: (user: { id: string; email: string }) => void;
}

type AuthMode = 'login' | 'register';

export default function AuthView({ onAuthenticated }: AuthViewProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');

  const handleSuccess = (data: TokenResponse) => {
    setStoredToken(data.access_token);
    localStorage.setItem('repomind_user', JSON.stringify({ id: data.user_id, email: data.email }));
    onAuthenticated({ id: data.user_id, email: data.email });
  };

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: handleSuccess,
    onError: (err: Error) => setError(err.message),
  });

  const registerMutation = useMutation({
    mutationFn: register,
    onSuccess: handleSuccess,
    onError: (err: Error) => setError(err.message),
  });

  const isPending = loginMutation.isPending || registerMutation.isPending;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email.trim() || !password.trim()) {
      setError('Please fill in all fields.');
      return;
    }

    if (mode === 'register') {
      if (password.length < 8) {
        setError('Password must be at least 8 characters.');
        return;
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match.');
        return;
      }
      registerMutation.mutate({ email: email.trim(), password });
    } else {
      loginMutation.mutate({ email: email.trim(), password });
    }
  };

  const switchMode = (newMode: AuthMode) => {
    setMode(newMode);
    setError('');
    setPassword('');
    setConfirmPassword('');
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden">
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/20 rounded-full blur-3xl mix-blend-screen opacity-50" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl mix-blend-screen opacity-50" />

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        className="glass-panel max-w-md w-full p-8 md:p-10 rounded-2xl relative z-10"
      >
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary to-purple-600 flex items-center justify-center shadow-lg shadow-primary/30">
            <Code2 className="w-7 h-7 text-white" />
          </div>
        </div>

        <h1 className="text-3xl font-bold text-center mb-2 tracking-tight">
          Repo<span className="text-primary">Mind</span>
        </h1>
        <p className="text-textMuted text-center text-sm mb-8">
          {mode === 'login' ? 'Sign in to access your repositories' : 'Create your RepoMind account'}
        </p>

        {/* Mode tabs */}
        <div className="flex rounded-xl bg-surface/50 p-1 mb-8 gap-1">
          <button
            id="auth-login-tab"
            type="button"
            onClick={() => switchMode('login')}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'login'
                ? 'bg-primary text-white shadow-md shadow-primary/30'
                : 'text-textMuted hover:text-textPrimary'
            }`}
          >
            <LogIn className="w-4 h-4" />
            Sign In
          </button>
          <button
            id="auth-register-tab"
            type="button"
            onClick={() => switchMode('register')}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'register'
                ? 'bg-primary text-white shadow-md shadow-primary/30'
                : 'text-textMuted hover:text-textPrimary'
            }`}
          >
            <UserPlus className="w-4 h-4" />
            Register
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Email */}
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
              <Mail className="h-4 w-4 text-textMuted group-focus-within:text-primary transition-colors" />
            </div>
            <input
              id="auth-email-input"
              type="email"
              placeholder="your@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field pl-11 h-12 bg-surface/50 backdrop-blur-sm"
              disabled={isPending}
              autoComplete="email"
            />
          </div>

          {/* Password */}
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
              <Lock className="h-4 w-4 text-textMuted group-focus-within:text-primary transition-colors" />
            </div>
            <input
              id="auth-password-input"
              type="password"
              placeholder={mode === 'register' ? 'Password (min 8 chars)' : 'Password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field pl-11 h-12 bg-surface/50 backdrop-blur-sm"
              disabled={isPending}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          </div>

          {/* Confirm password (register only) */}
          <AnimatePresence>
            {mode === 'register' && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="relative group">
                  <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                    <Lock className="h-4 w-4 text-textMuted group-focus-within:text-primary transition-colors" />
                  </div>
                  <input
                    id="auth-confirm-password-input"
                    type="password"
                    placeholder="Confirm password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="input-field pl-11 h-12 bg-surface/50 backdrop-blur-sm"
                    disabled={isPending}
                    autoComplete="new-password"
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Error message */}
          <AnimatePresence>
            {error && (
              <motion.p
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="text-error text-sm px-1"
              >
                {error}
              </motion.p>
            )}
          </AnimatePresence>

          <button
            id="auth-submit-btn"
            type="submit"
            disabled={!email || !password || isPending}
            className="w-full h-12 btn-primary flex items-center justify-center gap-2 mt-2"
          >
            {isPending ? (
              <span className="flex items-center gap-2">
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                {mode === 'login' ? 'Signing in...' : 'Creating account...'}
              </span>
            ) : (
              <>
                {mode === 'login' ? <LogIn className="w-4 h-4" /> : <UserPlus className="w-4 h-4" />}
                {mode === 'login' ? 'Sign In' : 'Create Account'}
              </>
            )}
          </button>
        </form>

        <p className="text-center text-xs text-textMuted mt-6">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            type="button"
            onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
            className="text-primary hover:underline"
          >
            {mode === 'login' ? 'Register' : 'Sign in'}
          </button>
        </p>
      </motion.div>
    </div>
  );
}
