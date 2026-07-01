import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bar, Line } from 'react-chartjs-2';
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';
import {
  deletePriority,
  enrollUser,
  fetchPriorities,
  fetchUsageSummary,
  listEnrollments,
  setPriority,
  testEmail,
  testSms,
  unenrollUser,
  type Enrollment,
  type EnrollmentRequest,
  type Priority,
  type UsageSummary,
} from '../api';

const E164 = /^\+[1-9]\d{1,14}$/;

const COMMON_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Tokyo',
  'UTC',
];

const EMPTY_FORM: EnrollmentRequest = {
  username: '',
  email: null,
  phone_number: null,
  sms_enabled: false,
  email_enabled: false,
  timezone: 'America/New_York',
};

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Tooltip,
  Legend,
);

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return (
    d.toLocaleString('en-GB', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    }) + ' Z'
  );
}

export default function AdminPage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchUsageSummary(days)
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const kpis = useMemo(() => {
    if (!summary) return null;
    const totalSuccess = summary.logins_per_day.reduce((s, d) => s + d.success, 0);
    const totalFailure = summary.logins_per_day.reduce((s, d) => s + d.failure, 0);
    const totalHits = summary.top_features.reduce((s, f) => s + f.hits, 0);
    const uniqueUsers = summary.top_users.length;
    return { totalSuccess, totalFailure, totalHits, uniqueUsers };
  }, [summary]);

  const loginChart = useMemo(() => {
    if (!summary) return null;
    return {
      labels: summary.logins_per_day.map((d) => d.day),
      datasets: [
        {
          label: 'Successful logins',
          data: summary.logins_per_day.map((d) => d.success),
          borderColor: 'rgb(88, 166, 255)',
          backgroundColor: 'rgba(88, 166, 255, 0.15)',
          tension: 0.3,
          fill: true,
        },
        {
          label: 'Failed attempts',
          data: summary.logins_per_day.map((d) => d.failure),
          borderColor: 'rgb(240, 99, 99)',
          backgroundColor: 'rgba(240, 99, 99, 0.1)',
          tension: 0.3,
          fill: true,
        },
      ],
    };
  }, [summary]);

  const featureChart = useMemo(() => {
    if (!summary || summary.top_features.length === 0) return null;
    return {
      labels: summary.top_features.map((f) => f.feature),
      datasets: [
        {
          label: 'Requests',
          data: summary.top_features.map((f) => f.hits),
          backgroundColor: 'rgba(88, 166, 255, 0.55)',
          borderColor: 'rgb(88, 166, 255)',
          borderWidth: 1,
        },
      ],
    };
  }, [summary]);

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: 'rgba(255,255,255,0.7)', font: { size: 11 } } },
      tooltip: { backgroundColor: '#0b2451', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1 },
    },
    scales: {
      x: {
        ticks: { color: 'rgba(255,255,255,0.7)', font: { size: 10 } },
        grid: { color: 'rgba(255,255,255,0.1)' },
      },
      y: {
        ticks: { color: 'rgba(255,255,255,0.7)', font: { size: 10 } },
        grid: { color: 'rgba(255,255,255,0.1)' },
        beginAtZero: true,
      },
    },
  };

  const endpointsFiltered = useMemo(() => {
    if (!summary) return [];
    if (!selectedTab) return summary.top_endpoints;
    return summary.top_endpoints.filter((e) => e.feature === selectedTab);
  }, [summary, selectedTab]);

  if (error) {
    return (
      <div className="p-8">
        <div className="bg-error-container/20 border border-error/30 rounded-lg p-4 text-sm text-error">
          Failed to load admin data: {error}
        </div>
      </div>
    );
  }

  if (!summary || !kpis) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-96px)]">
        <span className="material-symbols-outlined text-3xl text-primary animate-spin">
          progress_activity
        </span>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-headline font-bold uppercase tracking-widest text-on-surface">
            Admin — Usage
          </h1>
          <p className="text-xs text-outline mt-1">
            Logins and feature hits over the last {days} days
          </p>
        </div>
        <div className="flex items-center gap-2">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest transition-all ${
                days === d
                  ? 'bg-primary-container text-on-primary-container'
                  : 'bg-surface-container text-outline hover:text-on-surface'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Successful logins" value={kpis.totalSuccess} icon="login" />
        <KpiCard
          label="Failed attempts"
          value={kpis.totalFailure}
          icon="gpp_bad"
          accent={kpis.totalFailure > 0 ? 'warn' : undefined}
        />
        <KpiCard label="Unique users" value={kpis.uniqueUsers} icon="group" />
        <KpiCard label="Total requests" value={kpis.totalHits} icon="bolt" />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Logins per day">
          <div className="h-[280px]">
            {loginChart && <Line data={loginChart} options={chartOptions} />}
          </div>
        </Card>
        <Card title="Hits per tab">
          <div className="h-[280px]">
            {featureChart ? (
              <Bar data={featureChart} options={chartOptions} />
            ) : (
              <EmptyState message="No feature data yet. Use the app, then refresh." />
            )}
          </div>
        </Card>
      </div>

      {/* Tab breakdown — click a tab to filter the endpoint table below */}
      <Card title="Tab usage">
        {summary.top_features.length === 0 ? (
          <EmptyState message="No tab usage recorded yet." />
        ) : (
          <div className="flex flex-wrap gap-2">
            <FilterChip
              label="All"
              active={selectedTab === null}
              onClick={() => setSelectedTab(null)}
            />
            {summary.top_features.map((f) => (
              <FilterChip
                key={f.feature}
                label={f.feature}
                count={f.hits}
                active={selectedTab === f.feature}
                onClick={() => setSelectedTab(selectedTab === f.feature ? null : f.feature)}
              />
            ))}
          </div>
        )}
      </Card>

      {/* Endpoints table */}
      <Card title={selectedTab ? `Endpoints — ${selectedTab}` : 'All endpoints'}>
        {endpointsFiltered.length === 0 ? (
          <EmptyState
            message={
              selectedTab
                ? `No endpoint hits recorded for ${selectedTab}.`
                : 'No endpoint data yet.'
            }
          />
        ) : (
          <table className="w-full text-xs">
            <thead className="text-outline uppercase tracking-wider text-[10px]">
              <tr className="border-b border-outline-variant/15">
                <th className="text-left py-2 font-headline font-bold">Tab</th>
                <th className="text-left py-2 font-headline font-bold">Method</th>
                <th className="text-left py-2 font-headline font-bold">Path</th>
                <th className="text-right py-2 font-headline font-bold">Hits</th>
                <th className="text-right py-2 font-headline font-bold">Users</th>
              </tr>
            </thead>
            <tbody>
              {endpointsFiltered.map((e, i) => (
                <tr key={i} className="border-b border-outline-variant/10">
                  <td className="py-2 text-outline">{e.feature}</td>
                  <td className="py-2 text-primary font-mono text-[10px]">{e.method}</td>
                  <td className="py-2 text-on-surface font-mono text-[10px] break-all">{e.path}</td>
                  <td className="py-2 text-right text-on-surface tabular-nums">{e.hits}</td>
                  <td className="py-2 text-right text-outline tabular-nums">{e.unique_users}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Users + recent logins */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Top users">
          {summary.top_users.length === 0 ? (
            <EmptyState message="No usage recorded yet." />
          ) : (
            <table className="w-full text-xs">
              <thead className="text-outline uppercase tracking-wider text-[10px]">
                <tr className="border-b border-outline-variant/15">
                  <th className="text-left py-2 font-headline font-bold">User</th>
                  <th className="text-right py-2 font-headline font-bold">Events</th>
                  <th className="text-right py-2 font-headline font-bold">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {summary.top_users.map((u) => (
                  <tr key={u.username} className="border-b border-outline-variant/10">
                    <td className="py-2 text-on-surface">{u.username}</td>
                    <td className="py-2 text-right text-on-surface tabular-nums">{u.events}</td>
                    <td className="py-2 text-right text-outline">{formatTimestamp(u.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Recent logins">
          {summary.recent_logins.length === 0 ? (
            <EmptyState message="No login attempts recorded." />
          ) : (
            <div className="max-h-[320px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="text-outline uppercase tracking-wider text-[10px] sticky top-0 bg-surface-container">
                  <tr className="border-b border-outline-variant/15">
                    <th className="text-left py-2 font-headline font-bold">When</th>
                    <th className="text-left py-2 font-headline font-bold">User</th>
                    <th className="text-left py-2 font-headline font-bold">Result</th>
                    <th className="text-left py-2 font-headline font-bold">IP</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.recent_logins.map((row, i) => (
                    <tr key={i} className="border-b border-outline-variant/10">
                      <td className="py-2 text-outline">{formatTimestamp(row.timestamp)}</td>
                      <td className="py-2 text-on-surface">{row.username}</td>
                      <td
                        className={`py-2 ${row.status_code === 200 ? 'text-primary' : 'text-error'}`}
                      >
                        {row.status_code === 200
                          ? row.detail === 'admin'
                            ? 'admin'
                            : 'success'
                          : 'failed'}
                      </td>
                      <td className="py-2 text-outline font-mono text-[10px]">
                        {row.client_ip ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <CollectionPriorities />

      <NotificationsEnrollment />
    </div>
  );
}

// --- Team-wide collection priorities (issue #32) ---------------------------

const PRIORITY_LEVELS: Array<'country' | 'sector' | 'company'> = ['country', 'sector', 'company'];

function CollectionPriorities() {
  const [items, setItems] = useState<Priority[]>([]);
  const [level, setLevel] = useState<'country' | 'sector' | 'company'>('company');
  const [key, setKey] = useState('');
  const [weight, setWeight] = useState(5);
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await fetchPriorities();
      setItems(res.priorities);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const add = async () => {
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await setPriority({ level, key: key.trim(), weight, label: label.trim() });
      setKey('');
      setLabel('');
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await deletePriority(id);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card title="Collection Priorities — team-wide (boosts matching risk-feed items)">
      {error && <div className="mb-3 text-xs text-error">{error}</div>}

      {/* Add form */}
      <div className="flex flex-wrap items-end gap-2 mb-4">
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-outline">
          Level
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as 'country' | 'sector' | 'company')}
            className="bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface capitalize"
          >
            {PRIORITY_LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-outline flex-1 min-w-[160px]">
          Key (e.g. CN, semiconductor, Huawei)
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="match value"
            className="bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-outline w-28">
          Weight
          <input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
            className="bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-outline flex-1 min-w-[120px]">
          Label (optional)
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="display name"
            className="bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface"
          />
        </label>
        <button
          onClick={() => void add()}
          disabled={busy || !key.trim()}
          className="bg-primary-container text-on-primary-container text-xs font-bold uppercase tracking-widest px-4 py-2.5 rounded-lg hover:brightness-110 disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Add'}
        </button>
      </div>

      {/* List grouped by level */}
      {items.length === 0 ? (
        <EmptyState message="No priorities set. Add country / sector / company targets above." />
      ) : (
        <div className="space-y-3">
          {PRIORITY_LEVELS.map((lvl) => {
            const rows = items.filter((p) => p.level === lvl);
            if (rows.length === 0) return null;
            return (
              <div key={lvl}>
                <div className="text-[10px] uppercase tracking-wider text-outline mb-1 capitalize">
                  {lvl}
                </div>
                <div className="flex flex-wrap gap-2">
                  {rows.map((p) => (
                    <span
                      key={p.id}
                      className="flex items-center gap-2 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-1.5 text-xs text-on-surface"
                    >
                      <span className="font-medium">{p.label || p.key}</span>
                      <span className="text-[10px] text-primary font-mono">w{p.weight}</span>
                      <button
                        onClick={() => void remove(p.id)}
                        className="text-outline hover:text-error"
                        title="Remove priority"
                      >
                        <span className="material-symbols-outlined text-sm">close</span>
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface-container rounded-xl border border-outline-variant/15 p-4">
      <h3 className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

function KpiCard({
  label,
  value,
  icon,
  accent,
}: {
  label: string;
  value: string | number;
  icon: string;
  accent?: 'warn';
}) {
  const accentClass = accent === 'warn' ? 'text-tertiary' : 'text-primary';
  return (
    <div className="bg-surface-container rounded-xl border border-outline-variant/15 p-4">
      <div className="flex items-center gap-2 text-outline">
        <span className={`material-symbols-outlined text-base ${accentClass}`}>{icon}</span>
        <span className="text-[10px] font-headline font-bold uppercase tracking-[0.15em]">
          {label}
        </span>
      </div>
      <div className="text-2xl font-headline font-bold text-on-surface mt-2 tabular-nums">
        {value}
      </div>
    </div>
  );
}

function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest transition-all flex items-center gap-2 ${
        active
          ? 'bg-primary-container text-on-primary-container'
          : 'bg-surface-container-highest text-outline hover:text-on-surface'
      }`}
    >
      <span>{label}</span>
      {count !== undefined && <span className="tabular-nums opacity-70">{count}</span>}
    </button>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="h-full flex items-center justify-center text-xs text-outline text-center px-4 py-8">
      {message}
    </div>
  );
}

function NotificationsEnrollment() {
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [form, setForm] = useState<EnrollmentRequest>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  // Identifies which test-send button is in flight, e.g. "alice:sms" or "alice:email".
  const [testInFlight, setTestInFlight] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setEnrollments(await listEnrollments());
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const phoneInvalid = !!form.phone_number && !E164.test(form.phone_number);
  const smsNoPhone = form.sms_enabled && !form.phone_number;
  const emailNoEmail = form.email_enabled && !form.email;
  const canSubmit =
    form.username.trim().length > 0 && !phoneInvalid && !smsNoPhone && !emailNoEmail && !saving;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSavedMessage(null);
    try {
      const result = await enrollUser({
        ...form,
        username: form.username.trim(),
        email: form.email?.trim() || null,
        phone_number: form.phone_number?.trim() || null,
      });
      setSavedMessage(`Enrolled ${result.username}.`);
      setForm(EMPTY_FORM);
      await refresh();
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setSaving(false);
    }
  };

  const removeEnrollment = async (username: string) => {
    if (!confirm(`Remove notification enrollment for ${username}?`)) return;
    setError(null);
    try {
      await unenrollUser(username);
      await refresh();
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    }
  };

  const editEnrollment = (e: Enrollment) => {
    setForm({
      username: e.username,
      email: e.email,
      phone_number: e.phone_number,
      sms_enabled: e.sms_enabled,
      email_enabled: e.email_enabled,
      timezone: e.timezone,
    });
    setSavedMessage(null);
    setError(null);
  };

  const handleTestSms = async (username: string) => {
    setTestInFlight(`${username}:sms`);
    setError(null);
    setSavedMessage(null);
    try {
      const r = await testSms(username);
      if (r.status === 'sent') {
        setSavedMessage(
          `Test SMS sent to ${username} (Twilio SID: ${r.provider_message_id ?? 'n/a'}).`,
        );
      } else {
        // Backend returned 200 but the underlying send was gated/failed.
        setError(`Test SMS to ${username}: ${r.status}${r.error ? ` — ${r.error}` : ''}`);
      }
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setTestInFlight(null);
    }
  };

  const handleTestEmail = async (username: string) => {
    setTestInFlight(`${username}:email`);
    setError(null);
    setSavedMessage(null);
    try {
      const r = await testEmail(username);
      if (r.status === 'sent') {
        setSavedMessage(
          `Test email sent to ${username} (SendGrid ID: ${r.provider_message_id ?? 'n/a'}). Check inbox + spam.`,
        );
      } else {
        setError(`Test email to ${username}: ${r.status}${r.error ? ` — ${r.error}` : ''}`);
      }
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setTestInFlight(null);
    }
  };

  return (
    <section className="mt-12">
      <h2 className="text-lg font-headline font-bold text-on-surface mb-1">
        Notifications Enrollment
      </h2>
      <p className="text-sm text-outline mb-6">
        Enroll users to receive SMS alerts and the weekly email digest. End-users cannot change
        these settings themselves &mdash; only an admin can.
      </p>

      <form onSubmit={submit} className="bg-surface-container-low rounded-2xl p-6 mb-8">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label
              htmlFor="enroll-username"
              className="text-[10px] font-label uppercase tracking-widest text-outline mb-1.5 block"
            >
              Username
            </label>
            <input
              id="enroll-username"
              type="text"
              required
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm focus:border-primary/50 focus:outline-none"
              placeholder="e.g. alice"
              value={form.username}
              onChange={(ev) => setForm({ ...form, username: ev.target.value })}
            />
          </div>

          <div>
            <label
              htmlFor="enroll-timezone"
              className="text-[10px] font-label uppercase tracking-widest text-outline mb-1.5 block"
            >
              Timezone
            </label>
            <select
              id="enroll-timezone"
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm focus:border-primary/50 focus:outline-none"
              value={form.timezone}
              onChange={(ev) => setForm({ ...form, timezone: ev.target.value })}
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label
              htmlFor="enroll-email"
              className="text-[10px] font-label uppercase tracking-widest text-outline mb-1.5 block"
            >
              Email
            </label>
            <input
              id="enroll-email"
              type="email"
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm focus:border-primary/50 focus:outline-none"
              placeholder="user@example.com"
              value={form.email ?? ''}
              onChange={(ev) => setForm({ ...form, email: ev.target.value || null })}
            />
            <label className="flex items-center gap-2 mt-2 text-sm text-on-surface cursor-pointer">
              <input
                type="checkbox"
                checked={form.email_enabled}
                onChange={(ev) => setForm({ ...form, email_enabled: ev.target.checked })}
              />
              Weekly email digest
            </label>
            {emailNoEmail && (
              <p className="text-xs text-error mt-1">Email is required to enable digest.</p>
            )}
          </div>

          <div>
            <label
              htmlFor="enroll-phone"
              className="text-[10px] font-label uppercase tracking-widest text-outline mb-1.5 block"
            >
              Phone{' '}
              <span className="text-outline normal-case font-normal tracking-normal">
                (E.164, e.g. +12025551234)
              </span>
            </label>
            <input
              id="enroll-phone"
              type="tel"
              className={`w-full bg-surface-container-lowest border ${phoneInvalid ? 'border-error' : 'border-outline-variant/20'} rounded-lg px-3 py-2 text-sm focus:border-primary/50 focus:outline-none`}
              placeholder="+12025551234"
              value={form.phone_number ?? ''}
              onChange={(ev) => setForm({ ...form, phone_number: ev.target.value || null })}
            />
            {phoneInvalid && (
              <p className="text-xs text-error mt-1">Must start with + and country code.</p>
            )}
            <label className="flex items-center gap-2 mt-2 text-sm text-on-surface cursor-pointer">
              <input
                type="checkbox"
                checked={form.sms_enabled}
                onChange={(ev) => setForm({ ...form, sms_enabled: ev.target.checked })}
              />
              HIGH-severity SMS alerts
            </label>
            {smsNoPhone && (
              <p className="text-xs text-error mt-1">Phone is required to enable SMS.</p>
            )}
          </div>
        </div>

        {error && (
          <div className="bg-error/10 border border-error/30 text-error rounded-lg px-3 py-2 text-sm mt-4">
            {error}
          </div>
        )}
        {savedMessage && !error && (
          <div className="bg-primary/10 border border-primary/30 text-primary rounded-lg px-3 py-2 text-sm mt-4">
            {savedMessage}
          </div>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <button
            type="button"
            onClick={() => {
              setForm(EMPTY_FORM);
              setError(null);
              setSavedMessage(null);
            }}
            className="px-4 py-2 text-sm text-on-surface-variant hover:text-on-surface"
            disabled={saving}
          >
            Clear
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg text-xs font-bold hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Enroll / Update'}
          </button>
        </div>
      </form>

      <div className="bg-surface-container-low rounded-2xl p-6">
        <div className="flex items-baseline justify-between mb-4">
          <h3 className="text-sm font-bold text-on-surface">
            Currently enrolled ({enrollments.length})
          </h3>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="text-xs text-primary hover:text-primary/80"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        {enrollments.length === 0 && !loading && (
          <p className="text-sm text-outline">No users enrolled yet.</p>
        )}
        {enrollments.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-label uppercase tracking-widest text-outline">
                  <th className="pb-2">User</th>
                  <th className="pb-2">Email</th>
                  <th className="pb-2">Phone</th>
                  <th className="pb-2 text-center">SMS</th>
                  <th className="pb-2 text-center">Email</th>
                  <th className="pb-2">Timezone</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {enrollments.map((e) => (
                  <tr key={e.username} className="border-t border-outline-variant/10">
                    <td className="py-2 font-medium">{e.username}</td>
                    <td className="py-2 text-outline">{e.email ?? '—'}</td>
                    <td className="py-2 text-outline">{e.phone_number ?? '—'}</td>
                    <td className="py-2 text-center">{e.sms_enabled ? '✓' : ''}</td>
                    <td className="py-2 text-center">{e.email_enabled ? '✓' : ''}</td>
                    <td className="py-2 text-outline">{e.timezone}</td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => void handleTestSms(e.username)}
                        disabled={!e.sms_enabled || !e.phone_number || testInFlight !== null}
                        className="text-xs text-primary hover:text-primary/80 mr-3 disabled:opacity-40 disabled:cursor-not-allowed"
                        title={
                          !e.sms_enabled
                            ? 'SMS not enabled for this user'
                            : !e.phone_number
                              ? 'No phone number set'
                              : 'Send a test SMS to this user'
                        }
                      >
                        {testInFlight === `${e.username}:sms` ? 'Sending…' : 'Test SMS'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleTestEmail(e.username)}
                        disabled={!e.email_enabled || !e.email || testInFlight !== null}
                        className="text-xs text-primary hover:text-primary/80 mr-3 disabled:opacity-40 disabled:cursor-not-allowed"
                        title={
                          !e.email_enabled
                            ? 'Email not enabled for this user'
                            : !e.email
                              ? 'No email set'
                              : 'Send a test email to this user'
                        }
                      >
                        {testInFlight === `${e.username}:email` ? 'Sending…' : 'Test Email'}
                      </button>
                      <button
                        type="button"
                        onClick={() => editEnrollment(e)}
                        className="text-xs text-primary hover:text-primary/80 mr-3"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeEnrollment(e.username)}
                        className="text-xs text-error hover:text-error/80"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
