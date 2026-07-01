import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './layout/AppShell';
import { getToken } from './api';
import { useAuth } from './hooks/useAuth';

const SearchPage = lazy(() => import('./pages/SearchPage'));
const COAWorkspacePage = lazy(() => import('./pages/COAWorkspacePage'));
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'));
const BriefingsPage = lazy(() => import('./pages/BriefingsPage'));
const WargamePage = lazy(() => import('./pages/WargamePage'));
const RiskFeedPage = lazy(() => import('./pages/RiskFeedPage'));
const KnowledgeGraphPage = lazy(() => import('./pages/KnowledgeGraphPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const AdminPage = lazy(() => import('./pages/AdminPage'));

function RequireAuth({ children }: { children: React.ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return children;
}

function RequireAdmin({ children }: { children: React.ReactElement }) {
  const { isAdmin, loading } = useAuth();
  if (!getToken()) return <Navigate to="/login" replace />;
  if (loading) return null;
  if (!isAdmin) return <Navigate to="/search" replace />;
  return children;
}

export default function App() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/risk-feed" replace />} />
          <Route path="risk-feed" element={<RiskFeedPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="knowledge-graph" element={<KnowledgeGraphPage />} />
          <Route path="coa" element={<COAWorkspacePage />} />
          <Route path="monitoring" element={<MonitoringPage />} />
          <Route path="briefings" element={<BriefingsPage />} />
          <Route path="wargame" element={<WargamePage />} />
          <Route
            path="admin"
            element={
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            }
          />
        </Route>
      </Routes>
    </Suspense>
  );
}
