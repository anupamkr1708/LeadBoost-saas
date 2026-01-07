// src/pages/Dashboard.jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useLeads } from '../hooks/useLeads';
import { getOrganization, getUserPlan } from '../api/client';
import ScoreBadge from '../components/ScoreBadge';

export default function Dashboard() {
  const { user, isAuthenticated } = useAuth();
  const { leads, loading: leadsLoading, fetchLeads } = useLeads();
  const [organization, setOrganization] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [planInfo, setPlanInfo] = useState(null);
  const [planLoading, setPlanLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAuthenticated) {
      navigate('/login');
      return;
    }

    const fetchOrg = async () => {
      try {
        const org = await getOrganization();
        setOrganization(org);
      } catch (error) {
        console.error('Error fetching organization:', error);
      } finally {
        setOrgLoading(false);
      }
    };

    const fetchPlan = async () => {
      try {
        const plan = await getUserPlan();
        setPlanInfo(plan);
      } catch (error) {
        console.error('Error fetching plan info:', error);
      } finally {
        setPlanLoading(false);
      }
    };

    fetchOrg();
    fetchPlan();
  }, [isAuthenticated, navigate]);

  const recentLeads = leads.slice(0, 5);

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-bold text-gray-900">
            Dashboard
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            Welcome back, {user?.first_name || user?.email}!
          </p>
        </div>
      </header>

      <main className="py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          {/* Stats */}
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4 mb-8">
            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="px-4 py-5 sm:p-6">
                <div className="flex items-center">
                  <div className="flex-shrink-0 bg-blue-500 rounded-md p-3">
                    <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                    </svg>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Total Leads</dt>
                      <dd className="text-2xl font-semibold text-gray-900">
                        {orgLoading ? '...' : organization?.usage_count || 0}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="px-4 py-5 sm:p-6">
                <div className="flex items-center">
                  <div className="flex-shrink-0 bg-green-500 rounded-md p-3">
                    <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Active Leads</dt>
                      <dd className="text-2xl font-semibold text-gray-900">
                        {leads.filter(lead => lead.is_active).length}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="px-4 py-5 sm:p-6">
                <div className="flex items-center">
                  <div className="flex-shrink-0 bg-yellow-500 rounded-md p-3">
                    <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                    </svg>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Avg. Score</dt>
                      <dd className="text-2xl font-semibold text-gray-900">
                        {leads.length > 0 
                          ? (leads.reduce((sum, lead) => sum + (lead.score || 0), 0) / leads.length).toFixed(1) 
                          : '0.0'
                        }
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="px-4 py-5 sm:p-6">
                <div className="flex items-center">
                  <div className="flex-shrink-0 bg-purple-500 rounded-md p-3">
                    <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Plan</dt>
                      <dd className="text-2xl font-semibold text-gray-900">
                        {planLoading ? '...' : planInfo?.plan_name || 'Free'}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Recent Leads */}
          <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
            <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
              <h3 className="text-lg leading-6 font-medium text-gray-900">
                Recent Leads
              </h3>
              <p className="mt-1 text-sm text-gray-500">
                Your most recently processed leads
              </p>
            </div>
            <div className="px-4 py-5 sm:p-6">
              {leadsLoading ? (
                <div className="text-center py-8">
                  <div className="inline-block animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
                  <p className="mt-2 text-gray-600">Loading leads...</p>
                </div>
              ) : recentLeads.length === 0 ? (
                <div className="text-center py-8">
                  <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <h3 className="mt-2 text-sm font-medium text-gray-900">No leads yet</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    Get started by processing some company URLs.
                  </p>
                  <div className="mt-6">
                    <button
                      onClick={() => navigate('/finder')}
                      className="inline-flex items-center px-4 py-2 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                    >
                      Process Leads
                    </button>
                  </div>
                </div>
              ) : (
                <ul className="divide-y divide-gray-200">
                  {recentLeads.map((lead) => (
                    <li key={lead.id} className="py-4">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center">
                          <div className="text-sm font-medium text-gray-900">
                            {lead.website || 'Unknown Website'}
                          </div>
                          <div className="ml-4">
                            <ScoreBadge score={lead.score} label={lead.qualification_label} />
                          </div>
                        </div>
                        <div className="text-sm text-gray-500">
                          {lead.company_name || 'N/A'}
                        </div>
                      </div>
                      <div className="mt-2 text-sm text-gray-500">
                        <span className="font-medium">Industry:</span> {lead.industry || 'N/A'} • 
                        <span className="font-medium ml-2">Size:</span> {lead.employees || 'N/A'} • 
                        <span className="font-medium ml-2">Email:</span> {lead.email || 'N/A'}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}