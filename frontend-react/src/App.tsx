import { Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { AppShell } from '@/components/layout/AppShell'
import { LandingPage } from '@/pages/LandingPage'
import { LoginPage } from '@/pages/LoginPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { ChatPage } from '@/pages/ChatPage'
import { ChatHistoryPage } from '@/pages/ChatHistoryPage'
import { DocumentsPage } from '@/pages/DocumentsPage'
import { SearchPage } from '@/pages/SearchPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { EvaluationPage } from '@/pages/EvaluationPage'
import { MetricsPage } from '@/pages/MetricsPage'
import { UsersPage } from '@/pages/UsersPage'
import { RolesPage } from '@/pages/RolesPage'
import { AuditLogsPage } from '@/pages/AuditLogsPage'
import { TracesPage } from '@/pages/TracesPage'
import { AdminPage } from '@/pages/AdminPage'
import { GuardrailPolicyPage } from '@/pages/GuardrailPolicyPage'

function App() {
  return (
    <Routes>
      <Route path="/welcome" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route
          path="/"
          element={
            <ProtectedRoute permission="CHAT">
              <ChatPage />
            </ProtectedRoute>
          }
        />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route
          path="/history"
          element={
            <ProtectedRoute permission="VIEW_CONVERSATIONS">
              <ChatHistoryPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/documents"
          element={
            <ProtectedRoute permission="VIEW_DOCUMENTS">
              <DocumentsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/search"
          element={
            <ProtectedRoute permission="VIEW_DOCUMENTS">
              <SearchPage />
            </ProtectedRoute>
          }
        />
        <Route path="/settings" element={<SettingsPage />} />
        <Route
          path="/evaluation"
          element={
            <ProtectedRoute permission="VIEW_ANALYTICS" denyRoles={['hr', 'user']}>
              <EvaluationPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/metrics"
          element={
            <ProtectedRoute permission="VIEW_ANALYTICS">
              <MetricsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/users"
          element={
            <ProtectedRoute permission="VIEW_USERS">
              <UsersPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/roles"
          element={
            <ProtectedRoute permission="VIEW_ROLES">
              <RolesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/audit-logs"
          element={
            <ProtectedRoute permission="VIEW_AUDIT_LOGS">
              <AuditLogsPage />
            </ProtectedRoute>
          }
        />
        {/* No permission requirement — every authenticated role can open this,
            scoped server-side to their own history (routers/traces.py). */}
        <Route path="/traces" element={<TracesPage />} />
        <Route
          path="/admin"
          element={
            <ProtectedRoute permission="SYSTEM_SETTINGS">
              <AdminPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/guardrail-policies"
          element={
            <ProtectedRoute permission="MANAGE_GUARDRAIL_POLICIES">
              <GuardrailPolicyPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}

export default App
