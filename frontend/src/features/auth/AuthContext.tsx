import { createContext, useContext, useEffect, useState } from "react";

import { getMe, logout } from "../../api";
import type { User } from "../../types";

type AuthContextValue = {
  user: User | null;
  isRestoring: boolean;
  setSession: (user: User) => void;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);

  useEffect(() => {
    getMe()
      .then((hydrated) => setUser(hydrated))
      .catch(() => setUser(null))
      .finally(() => setIsRestoring(false));
  }, []);

  function setSession(nextUser: User) {
    setUser(nextUser);
  }

  async function signOut() {
    try {
      await logout();
    } finally {
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, isRestoring, setSession, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
