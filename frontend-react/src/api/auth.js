// src/api/auth.js
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

export const login = async (email, password) => {
  const params = new URLSearchParams();
  params.append('username', email);
  params.append('password', password);
  
  const response = await axios.post(`${API_BASE_URL}/api/v2/login`, params, {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });
  return response.data;
};

export const register = async (userData) => {
  const response = await api.post('/api/v2/register', userData);
  return response.data;
};

export const getCurrentUser = async (token) => {
  const response = await axios.get(`${API_BASE_URL}/api/v2/me`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  return response.data;
};

export const refreshToken = async (refreshToken) => {
  const response = await api.post('/api/v2/refresh', {
    refresh_token: refreshToken,
  });
  return response.data;
};

export const updateProfile = async (userData) => {
  const response = await api.put('/api/v2/me', userData);
  return response.data;
};

export const logout = async () => {
  // Currently, we just clear the token locally
  // In a real app, you might want to invalidate the token on the backend too
  localStorage.removeItem('token');
  return { success: true };
};

export default api;