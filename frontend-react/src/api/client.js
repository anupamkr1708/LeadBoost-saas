// src/api/client.js
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

// Create axios instance with default config
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor to include token in headers
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add response interceptor to handle token expiration
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear auth data if unauthorized
      localStorage.removeItem('token');
      window.location.href = '/login'; // Redirect to login
    }
    return Promise.reject(error);
  }
);

/**
 * Process leads (main backend API)
 */
export async function processLeads(urls, messageStyle = "professional") {
  const response = await api.post('/api/v2/leads', {
    urls,
    message_style: messageStyle,
  });
  return response.data;
}

/**
 * Get all leads for the current user's organization
 */
export async function getLeads(skip = 0, limit = 100) {
  const response = await api.get('/api/v2/leads/', {
    params: { skip, limit },
  });
  return response.data;
}

/**
 * Get a specific lead by ID
 */
export async function getLead(leadId) {
  const response = await api.get(`/api/v2/leads/${leadId}`);
  return response.data;
}

/**
 * Update a lead by ID
 */
export async function updateLead(leadId, leadData) {
  const response = await api.put(`/api/v2/leads/${leadId}`, leadData);
  return response.data;
}

/**
 * Delete a lead by ID
 */
export async function deleteLead(leadId) {
  const response = await api.delete(`/api/v2/leads/${leadId}`);
  return response.data;
}

/**
 * Manually trigger processing for a lead
 */
export async function processLeadNow(leadId) {
  const response = await api.post(`/api/v2/leads/${leadId}/process`);
  return response.data;
}

/**
 * Create a single lead
 */
export async function createLead(leadData) {
  const response = await api.post('/api/v2/leads/single', leadData);
  return response.data;
}

/**
 * Get current user's organization
 */
export async function getOrganization() {
  const response = await api.get('/api/v2/organizations');
  return response.data;
}

/**
 * Update organization
 */
export async function updateOrganization(orgId, orgData) {
  const response = await api.put(`/api/v2/organizations/${orgId}`, orgData);
  return response.data;
}

/**
 * Get available plans
 */
export async function getAvailablePlans() {
  const response = await api.get('/api/v2/plans');
  return response.data;
}

/**
 * Get usage details
 */
export async function getUsage() {
  const response = await api.get('/api/v2/usage');
  return response.data;
}

/**
 * Get user's subscription/plan details
 */
export async function getUserPlan() {
  const response = await api.get('/api/v2/usage');
  return response.data;
}

/**
 * Upgrade subscription plan
 */
export async function upgradePlan(planName) {
  const response = await api.post('/api/v2/upgrade', null, {
    params: { plan_name: planName }
  });
  return response.data;
}

/**
 * Cancel subscription
 */
export async function cancelSubscription(immediate = false) {
  const response = await api.post('/api/v2/cancel', null, {
    params: { immediate: immediate }
  });
  return response.data;
}


/**
 * Get usage records
 */
export async function getUsageRecords(startDate = null, endDate = null) {
  const response = await api.get('/api/v2/usage', {
    params: { start_date: startDate, end_date: endDate }
  });
  return response.data;
}

/**
 * Health check (optional)
 */
export async function healthCheck() {
  const response = await api.get('/health');
  return response.data;
}

export default api;