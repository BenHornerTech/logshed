import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { getAuthStatus, loginAdmin, logoutAdmin, setupAdmin } from '../api/auth.ts';

interface AuthContextType {
  isAuthenticated: boolean;
  setupRequired: boolean;
  isLoading: boolean;
  error: string | null;
  login: (password: string) => Promise<void>;
  setup: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [setupRequired, setSetupRequired] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refreshAuth = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const status = await getAuthStatus();
      setIsAuthenticated(status.authenticated);
      setSetupRequired(status.setup_required);
    } catch (err: any) {
      console.error('Failed to fetch auth status', err);
      setError(err.message || 'Failed to check authentication status.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  useEffect(() => {
    const handleUnauthorized = (e: Event) => {
      const customEvent = e as CustomEvent<{ message?: string }>;
      setIsAuthenticated(false);
      if (customEvent.detail?.message) {
        setError(customEvent.detail.message);
      }
    };
    window.addEventListener('logshed:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('logshed:unauthorized', handleUnauthorized);
  }, []);

  const login = async (password: string) => {
    setError(null);
    try {
      await loginAdmin(password);
      setIsAuthenticated(true);
      setSetupRequired(false);
    } catch (err: any) {
      setError(err.message || 'Login failed.');
      throw err;
    }
  };

  const setup = async (password: string) => {
    setError(null);
    try {
      await setupAdmin(password);
      setIsAuthenticated(true);
      setSetupRequired(false);
    } catch (err: any) {
      setError(err.message || 'Setup failed.');
      throw err;
    }
  };

  const logout = async () => {
    try {
      await logoutAdmin();
    } finally {
      setIsAuthenticated(false);
      window.location.reload();
    }
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        setupRequired,
        isLoading,
        error,
        login,
        setup,
        logout,
        refreshAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
