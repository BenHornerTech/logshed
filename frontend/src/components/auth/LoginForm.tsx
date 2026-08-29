import React, { useState } from 'react';
import { Lock, Activity, AlertCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';

export const LoginForm: React.FC = () => {
  const { login } = useAuth();
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;

    try {
      setIsLoading(true);
      setErrorMsg(null);
      await login(password);
    } catch (err: any) {
      setErrorMsg(err.message || 'Login failed. Please check your password.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-dark-950 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md bg-dark-900 border border-dark-700 rounded-xl p-8 shadow-2xl">
        {/* Brand Header */}
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-dark-800 border border-dark-700 flex items-center justify-center mb-3">
            <Activity className="w-6 h-6 text-accent-500" />
          </div>
          <h1 className="text-xl font-bold text-slate-100 tracking-wide">HOMELAB LOG HUB</h1>
          <p className="text-xs text-slate-400 mt-1">Single-Process Log Aggregator & Ops Console</p>
        </div>

        {errorMsg && (
          <div className="mb-4 p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start space-x-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Admin Password
            </label>
            <div className="relative">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password..."
                required
                autoFocus
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-4 py-2.5 pl-10 text-sm text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500 font-mono"
              />
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-3" />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading || !password.trim()}
            className="w-full bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium py-2.5 px-4 rounded-lg text-sm transition-all shadow-md flex items-center justify-center space-x-2"
          >
            {isLoading ? (
              <span>Authenticating...</span>
            ) : (
              <>
                <Lock className="w-4 h-4" />
                <span>Sign In to Console</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
