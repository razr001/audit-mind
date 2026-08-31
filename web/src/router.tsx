import { createRootRoute, createRoute, createRouter, lazyRouteComponent, Outlet, redirect } from '@tanstack/react-router'
import { getAccessToken } from './lib/auth-token'

const LoginPage = lazyRouteComponent(() => import('./pages/LoginPage'), 'LoginPage')
const RegulationListPage = lazyRouteComponent(
  () => import('./pages/regulation/RegulationListPage'),
  'RegulationListPage',
)
const RegulationDetailPage = lazyRouteComponent(
  () => import('./pages/regulation/RegulationDetailPage'),
  'RegulationDetailPage',
)
const AssistantPage = lazyRouteComponent(
  () => import('./pages/assistant/AssistantPage'),
  'AssistantPage',
)
const AuditListPage = lazyRouteComponent(() => import('./pages/audit/AuditListPage'), 'AuditListPage')
const AuditCreatePage = lazyRouteComponent(() => import('./pages/audit/AuditCreatePage'), 'AuditCreatePage')
const AuditDetailPage = lazyRouteComponent(() => import('./pages/audit/AuditDetailPage'), 'AuditDetailPage')
const UserManagementPage = lazyRouteComponent(() => import('./pages/users/UserManagementPage'), 'UserManagementPage')

const rootRoute = createRootRoute({ component: () => <Outlet /> })

const authenticatedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_authenticated',
  beforeLoad: requireAuthentication,
  component: () => <Outlet />,
})

const indexRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/audit', replace: true })
  },
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
})

const regulationRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/regulation',
  component: RegulationListPage,
})

const regulationDetailRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/regulation/$regulationId',
  component: RegulationDetailPage,
})

const assistantRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/assistant',
  component: AssistantPage,
})

const auditListRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/audit',
  component: AuditListPage,
})

const auditCreateRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/audit/new',
  component: AuditCreatePage,
})

const auditDetailRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/audit/$taskId',
  component: AuditDetailPage,
})

const usersRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: '/users',
  component: UserManagementPage,
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  authenticatedRoute.addChildren([indexRoute, regulationRoute, regulationDetailRoute, assistantRoute, auditListRoute, auditCreateRoute, auditDetailRoute, usersRoute]),
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

function requireAuthentication() {
  if (!getAccessToken()) throw redirect({ to: '/login', replace: true })
}
