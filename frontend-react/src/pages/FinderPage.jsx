// src/pages/FinderPage.jsx

import { useState, useEffect } from "react";
import { useLeads } from "../hooks/useLeads";
import { getUserPlan } from "../api/client";
import LeadCard from "../components/LeadCard";
import LoadingState from "../components/LoadingState";

export default function FinderPage() {
  const [urlsInput, setUrlsInput] = useState("");
  const [messageStyle, setMessageStyle] = useState("professional");
  const [planInfo, setPlanInfo] = useState(null);
  const [planLoading, setPlanLoading] = useState(true);

  const {
    leads,
    loading,
    error,
    runLeadProcessing,
  } = useLeads();

  useEffect(() => {
    const fetchPlan = async () => {
      try {
        const plan = await getUserPlan();
        setPlanInfo(plan);
      } catch (err) {
        console.error('Error fetching plan info:', err);
        // Default to free plan info if API fails
        setPlanInfo({
          plan_name: 'free',
          max_leads_per_day: 10,
          remaining_daily_leads: 10,
          can_process_more_today: true
        });
      } finally {
        setPlanLoading(false);
      }
    };

    fetchPlan();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();

    const urls = urlsInput
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);

    if (urls.length === 0) return;

    // Check if user can process the requested number of leads
    if (planInfo && !planInfo.can_process_more_today) {
      alert(`You've reached your daily limit of ${planInfo.max_leads_per_day} leads. Upgrade your plan or try again tomorrow.`);
      return;
    }

    if (planInfo && urls.length > planInfo.remaining_daily_leads) {
      alert(`You can only process ${planInfo.remaining_daily_leads} more leads today based on your plan. Please reduce the number of URLs or upgrade your plan.`);
      return;
    }

    await runLeadProcessing(urls, messageStyle);
  };

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Lead Intelligence Finder
        </h1>
        <p className="text-gray-600 mt-1">
          Enter company URLs to discover enriched leads, scores, and outreach messages.
        </p>
      </div>

      {/* Input Form */}
      <form
        onSubmit={handleSubmit}
        className="bg-white border rounded-lg p-4 space-y-4"
      >
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Company URLs (one per line)
          </label>
          <textarea
            rows={4}
            className="w-full border rounded-md p-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={`https://example.com\nhttps://another-company.com`}
            value={urlsInput}
            onChange={(e) => setUrlsInput(e.target.value)}
          />
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          {/* Message Style */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Message Style
            </label>
            <select
              value={messageStyle}
              onChange={(e) => setMessageStyle(e.target.value)}
              className="border rounded-md p-2 text-sm"
            >
              <option value="professional">Professional</option>
              <option value="friendly">Friendly</option>
              <option value="short">Short</option>
            </select>
          </div>

          {/* Plan Info */}
          <div className="text-sm text-gray-600">
            {planLoading ? (
              <span>Loading plan info...</span>
            ) : planInfo ? (
              <span>
                Plan: <span className="font-medium capitalize">{planInfo.plan_name}</span> | 
                Remaining: <span className="font-medium">{planInfo.remaining_daily_leads}</span>/{planInfo.max_leads_per_day} today
              </span>
            ) : (
              <span>Plan info unavailable</span>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || (planInfo && !planInfo.can_process_more_today)}
            className={`px-4 py-2 rounded-md text-sm font-medium ${
              planInfo && !planInfo.can_process_more_today
                ? 'bg-gray-400 text-white cursor-not-allowed'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            }`}
          >
            {loading ? "Processing..." : "Process Leads"}
          </button>
        </div>
      </form>

      {/* Results */}
      <div className="space-y-4">
        {error && (
          <div className="rounded-md bg-red-50 p-4">
            <div className="text-sm text-red-700">{error}</div>
          </div>
        )}
        
        {loading && (
          <div className="bg-white border rounded-lg p-6">
            <div className="flex items-center justify-center space-x-2">
              <div className="inline-block animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-blue-500"></div>
              <span className="text-gray-700">Processing leads... This may take a few moments.</span>
            </div>
          </div>
        )}

        <div className="space-y-4">
          {leads.map((lead, idx) => (
            <LeadCard key={lead.id || idx} lead={lead} />
          ))}
        </div>
        
        {!loading && leads.length === 0 && (
          <div className="text-center py-8">
            <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
            <h3 className="mt-2 text-sm font-medium text-gray-900">No leads yet</h3>
            <p className="mt-1 text-sm text-gray-500">
              Enter company URLs above to start processing leads.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
