import { ApiError, TIMEOUT_STATUS, TIMEOUT_MESSAGE } from "./errors";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api/v1";

/** Default request timeout in milliseconds (covers Render cold-start latency). */
const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * Core fetch wrapper.
 *
 * @param path        API path, e.g. "/auth/login"
 * @param options     Standard RequestInit options
 * @param timeoutMs   Per-request timeout override. Pass 0 to disable entirely.
 *                    Defaults to DEFAULT_TIMEOUT_MS (15 s).
 *
 * Throws ApiError on:
 *   - timeout          → err.isTimeout === true,  err.message = TIMEOUT_MESSAGE
 *   - network failure  → err.isNetworkError === true
 *   - non-2xx HTTP     → err.status = HTTP code, err.message = parsed detail
 */
async function request(
  path: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
) {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("token") : null;

  // ── AbortController wiring ─────────────────────────────────────────────────
  const controller = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => controller.abort("timeout"), timeoutMs);
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token && { Authorization: `Bearer ${token}` }),
        ...(options.headers || {}),
      },
    });
  } catch (err) {
    // Distinguish an AbortController timeout from a genuine network failure.
    // fetch rejects with a DOMException named "AbortError" on abort.
    if (
      err instanceof DOMException && err.name === "AbortError"
    ) {
      throw new ApiError(TIMEOUT_STATUS, TIMEOUT_MESSAGE);
    }
    // Network failure — no HTTP response at all (offline, DNS failure, CORS, etc.)
    throw new ApiError(0, "Unable to reach the server. Please check your connection.");
  } finally {
    // Always clear the timer to prevent it firing after a successful response.
    clearTimeout(timeoutId);
  }

  // Parse body — guard against non-JSON responses (e.g. 502 HTML gateway pages)
  let data: unknown;
  try {
    data = await res.json();
  } catch {
    // Response body is not valid JSON (e.g. empty 204, plain-text 500)
    if (!res.ok) throw new ApiError(res.status, null);
    return null;
  }

  if (!res.ok) {
    throw new ApiError(res.status, data);
  }

  return data;
}

export type LoginResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type RegisterResponse = {
  id: number;
  full_name: string;
  email: string;
  role: string;
  is_active: boolean;
};

export const auth = {
  login: (email: string, password: string): Promise<LoginResponse> =>
    request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  register: (data: { full_name: string; email: string; password: string }): Promise<RegisterResponse> =>
    request("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

export type Customer = {
  id: number;
  name: string;
  email: string;
  phone?: string;
  address?: string;
  company?: string;
  created_at?: string;
};

export type CustomerCreate = {
  name: string;
  email: string;
  phone?: string;
  address?: string;
  company?: string;
};

export const customers = {
  list: async () => {
    const data = await request("/customers");
    return data.map((c: any) => ({
      ...c,
      company: c.company || "",
      created_at: c.created_at || new Date().toISOString(),
    }));
  },

  create: (data: CustomerCreate) =>
    request("/customers", {
      method: "POST",
      body: JSON.stringify({
        name: data.name,
        email: data.email,
        phone: data.phone || "",
        address: data.address || "",
      }),
    }),

  update: (id: number, data: CustomerCreate) =>
    request(`/customers/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        name: data.name,
        email: data.email,
        phone: data.phone || "",
        address: data.address || "",
      }),
    }),

  delete: (id: number) =>
    request(`/customers/${id}`, {
      method: "DELETE",
    }),
};

export type InvoiceItemCreate = {
  description: string;
  quantity: number;
  unit_price: number;
};

export type Invoice = {
  id: number;
  invoice_number: string;
  customer_id: number;
  user_id: number;
  issue_date: string;
  due_date: string;
  status: string;
  total_amount: number;
  notes?: string;
  customer?: Customer;
};

export const invoices = {
  list: async () => {
    const [invoiceData, customerData] = await Promise.all([
      request("/invoices"),
      customers.list(),
    ]);

    

    return invoiceData.map((inv: any) => ({
      ...inv,
      due_date: inv.due_date || new Date().toISOString(),
      customer: customerData.find((c: Customer) => c.id === (inv.client_id ?? inv.customer_id)),
    }));
  },

  getAll: () => request("/invoices"),

  create: async (data: {
    customer_id: number;
    due_date: string;
    notes?: string;
    items: InvoiceItemCreate[];
  }) => {
    const invoice = await request("/invoices", {
      method: "POST",
      body: JSON.stringify({
        client_id: data.customer_id,
        due_date: `${data.due_date}T00:00:00`,
        notes: data.notes || "",
        items: data.items.filter(it => it.description.trim()),
      }),
    });

    return invoice;
  },

  updatePayment: (id: number, status: string) =>
    request(`/invoices/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  sendReminder: (id: number) =>
    request(`/invoices/${id}/send-reminder`, {
      method: "POST",
    }),



  aiFollowup: (id: number, tone: string = "polite") =>

    request(`/invoices/${id}/ai-followup?tone=${tone}`, {

      method: "POST",

    }),


  delete: (id: number) =>
    request(`/invoices/${id}`, {
      method: "DELETE",
    }),

  downloadPdf: (id: number) => `${BASE_URL}/invoices/${id}/pdf`,

  exportCsv: () => `${BASE_URL}/exports/invoices-csv`,
};

export const dashboard = {
  analytics: async () => {
    // Fan-out: 3 parallel requests. Allow 20 s to accommodate Render cold starts
    // plus the time for all three to complete.
    const [summary, invoiceList, monthlyData] = await Promise.all([
      request("/dashboard/summary", {}, 20_000),
      invoices.list(),
      request("/dashboard/monthly-revenue", {}, 20_000),
    ]);

    return {
      ...summary,
      pending_amount: summary.unpaid_amount || 0,
      recent_invoices: invoiceList.slice(-5).reverse(),
      monthly_revenue: monthlyData,
    };
  },
};

export type DashboardAnalytics = {
  total_customers: number;
  total_invoices: number;
  paid_invoices: number;
  draft_invoices: number;
  overdue_invoices: number;
  total_revenue: number;
  unpaid_amount: number;
  pending_amount: number;
  recent_invoices: Invoice[];
  monthly_revenue: { month: string; amount: number }[];
};



export type RecurringCreate = {
  client_id: number;
  title: string;
  description: string;
  amount: number;
  frequency: string;
  next_billing_date: string;
};

export type RecurringBilling = {
  id: number;
  client_id?: number;
  customer_id: number;
  user_id: number;
  title?: string;
  description: string;
  amount: number;
  frequency: string;
  next_billing_date: string;
  is_active: boolean;
  customer?: Customer;
};

export const recurring = {
  list: async () => {
    const [plans, customerData] = await Promise.all([
      request("/recurring-billing"),
      customers.list(),
    ]);

    return plans.map((p: any) => ({
      ...p,
      description: p.title,
      customer: customerData.find((c: Customer) => c.id === p.customer_id),
    }));
  },

  create: (data: RecurringCreate) =>
  request("/recurring-billing", {
    method: "POST",
    body: JSON.stringify({
      client_id: data.client_id || 0,
      title: data.description,
      amount: data.amount,
      frequency: data.frequency,
      next_billing_date: `${data.next_billing_date}T00:00:00`,
      is_active: true,
    }),
  }),

update: (id: number, data: RecurringCreate) =>
  request(`/recurring-billing/${id}`, {
    method: "PUT",
    body: JSON.stringify({
      client_id: data.client_id || 0,
      title: data.description,
      amount: data.amount,
      frequency: data.frequency,
      next_billing_date: `${data.next_billing_date}T00:00:00`,
      is_active: true,
    }),
  }),

  delete: (id: number) =>
    request(`/recurring-billing/${id}`, {
      method: "DELETE",
    }),

  generate: (id: number) =>
    request(`/recurring-billing/${id}/generate-invoice`, {
      method: "POST",
    }),
};