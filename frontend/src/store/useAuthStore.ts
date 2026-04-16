import { create } from "zustand";

interface AuthState {
  tenant_id: string;
  role: string;
  isAuthenticated: boolean;
  setAuth: (tenant_id: string, role: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  tenant_id: "institutional-alpha-1", // Default for development
  role: "Lead PM",
  isAuthenticated: true,
  setAuth: (tenant_id, role) => set({ tenant_id, role, isAuthenticated: true }),
  logout: () => set({ tenant_id: "", role: "", isAuthenticated: false }),
}));
