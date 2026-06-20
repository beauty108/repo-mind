import { useState, useEffect } from 'react';
import LandingView from './views/LandingView';
import IndexingView from './views/IndexingView';
import ChatView from './views/ChatView';
import AuthView from './views/AuthView';
import { getStoredToken, clearStoredToken } from './api';

type AppState = 'auth' | 'landing' | 'indexing' | 'chat';

interface AuthUser {
  id: string;
  email: string;
}

function App() {
  const [appState, setAppState] = useState<AppState>('auth');
  const [repoId, setRepoId] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);

  // On mount: check for existing auth token + saved repo state
  useEffect(() => {
    const token = getStoredToken();
    const savedUser = localStorage.getItem('repomind_user');

    if (token && savedUser) {
      try {
        const user = JSON.parse(savedUser);
        setCurrentUser(user);

        // Restore previous repo state if present
        const savedRepoId = localStorage.getItem('repomind_current_repo_id');
        const savedState = localStorage.getItem('repomind_current_state') as AppState;
        if (savedRepoId && savedState && savedState !== 'auth') {
          setRepoId(savedRepoId);
          setAppState(savedState);
        } else {
          setAppState('landing');
        }
      } catch {
        setAppState('auth');
      }
    } else {
      setAppState('auth');
    }
  }, []);

  const handleAuthenticated = (user: AuthUser) => {
    setCurrentUser(user);
    setAppState('landing');
  };

  const handleLogout = () => {
    clearStoredToken();
    setCurrentUser(null);
    setRepoId(null);
    setAppState('auth');
    localStorage.removeItem('repomind_current_repo_id');
    localStorage.removeItem('repomind_current_state');
  };

  const handleRepoSubmitted = (id: string) => {
    setRepoId(id);
    setAppState('indexing');
    localStorage.setItem('repomind_current_repo_id', id);
    localStorage.setItem('repomind_current_state', 'indexing');
  };

  const handleReady = () => {
    setAppState('chat');
    localStorage.setItem('repomind_current_state', 'chat');
  };

  const handleReset = () => {
    setRepoId(null);
    setAppState('landing');
    localStorage.removeItem('repomind_current_repo_id');
    localStorage.removeItem('repomind_current_state');
  };

  return (
    <div className="min-h-screen bg-background text-textMain">
      {appState === 'auth' && (
        <AuthView onAuthenticated={handleAuthenticated} />
      )}

      {appState === 'landing' && (
        <LandingView onRepoSubmitted={handleRepoSubmitted} />
      )}
      
      {appState === 'indexing' && repoId && (
        <IndexingView 
          repoId={repoId} 
          onReady={handleReady} 
          onReset={handleReset} 
        />
      )}
      
      {appState === 'chat' && repoId && (
        <ChatView
          repoId={repoId}
          onReset={handleReset}
          currentUser={currentUser}
          onLogout={handleLogout}
        />
      )}
    </div>
  );
}

export default App;
