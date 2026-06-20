import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe, clearToken } from '../api';

function ESTClock() {
  const [time, setTime] = useState('');
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTime(
        now.toLocaleTimeString('en-US', {
          timeZone: 'America/New_York',
          hour12: false,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }) + ' EST',
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="text-on-surface-variant px-2 py-1">{time}</span>;
}

export default function TopNav() {
  const navigate = useNavigate();
  const [username, setUsername] = useState<string>('');

  useEffect(() => {
    fetchMe()
      .then((res) => setUsername(res.username))
      .catch(() => {});
  }, []);

  function handleSignOut() {
    clearToken();
    navigate('/login', { replace: true });
  }

  return (
    <header className="fixed top-0 w-full z-50 flex justify-between items-center px-4 h-12 bg-surface-container-low border-b border-outline-variant font-headline tracking-tight text-sm">
      <div className="flex items-center gap-6">
        <span className="font-headline font-bold text-on-surface uppercase tracking-widest">
          Emissary
        </span>
        <nav className="hidden md:flex items-center gap-4">
          <ESTClock />
        </nav>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-primary font-medium">{username || 'Guest'}</span>
        <div className="w-8 h-8 rounded-full bg-surface-container-highest overflow-hidden border border-outline-variant/20 flex items-center justify-center">
          <span className="material-symbols-outlined text-outline text-sm">person</span>
        </div>
        <button
          onClick={handleSignOut}
          className="text-on-surface-variant hover:text-on-surface transition-colors"
          title="Sign out"
        >
          <span className="material-symbols-outlined text-lg">logout</span>
        </button>
      </div>
    </header>
  );
}
