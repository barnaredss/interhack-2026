import { createContext, useContext, useEffect, useState } from "react";
import { onAuthStateChanged } from "firebase/auth";
import { auth, getDriverId, loginDriver, logoutDriver } from "../firebase";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined); // undefined = loading
  const [driverId, setDriverId] = useState(null);

  useEffect(() => {
    return onAuthStateChanged(auth, (firebaseUser) => {
      setUser(firebaseUser ?? null);
      setDriverId(firebaseUser ? getDriverId(firebaseUser) : null);
    });
  }, []);

  async function login(id, password) {
    await loginDriver(id, password);
  }

  async function logout() {
    await logoutDriver();
  }

  return (
    <AuthContext.Provider value={{ user, driverId, login, logout, loading: user === undefined }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
