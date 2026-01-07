// src/pages/BillingPage.jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { 
  getAvailablePlans, 
  getUsage, 
  upgradePlan, 
  getUsageRecords,
  cancelSubscription
} from '../api/client';

export default function BillingPage() {
  const { user, isAuthenticated } = useAuth();
  const [subscription, setSubscription] = useState(null);
  const [usageRecords, setUsageRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [billingPortalLoading, setBillingPortalLoading] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState('');
  const [isCancelling, setIsCancelling] = useState(false);
  const [showCancelModal, setShowCancelModal] = useState(false);
  const [cancelImmediate, setCancelImmediate] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAuthenticated) {
      navigate('/login');
      return;
    }

    fetchData();
  }, [isAuthenticated, navigate]);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [usage, plans] = await Promise.all([
        getUsage().catch(() => null),
        getAvailablePlans().catch(() => [])
      ]);
      
      // Set the current usage as subscription data
      setSubscription(usage);
      setUsageRecords(plans || []);
    } catch (err) {
      setError('Failed to load billing information: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubscribe = async (planId) => {
    try {
      // For the free plan, we don't need to do anything special
      if (planId === 'free') {
        setSubscription({...subscription, plan_name: 'free', can_use_ai: false});
      }
    } catch (err) {
      setError('Failed to select plan: ' + err.message);
    }
  };

  const handleUpdatePlan = async (planId) => {
    try {
      const result = await upgradePlan(planId);
      // Refresh usage data after upgrade
      const updatedUsage = await getUsage();
      setSubscription(updatedUsage);
    } catch (err) {
      setError('Failed to update subscription: ' + err.message);
    }
  };

  const handleCancelSubscription = async (immediate = false) => {
    try {
      setIsCancelling(true);
      await cancelSubscription(immediate);
      // Refresh usage data after cancellation
      const updatedUsage = await getUsage();
      setSubscription(updatedUsage);
      setShowCancelModal(false);
      alert('Subscription cancelled successfully');
    } catch (err) {
      setError('Failed to cancel subscription: ' + err.message);
    } finally {
      setIsCancelling(false);
    }
  };

  // Manage billing functionality is not implemented in backend
  const handleManageBilling = async () => {
    alert('Manage billing is not available in this version');
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
          <p className="mt-2 text-gray-600">Loading billing information...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <div className="max-w-md w-full bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-xl font-bold text-red-600 mb-4">Error</h2>
          <p className="text-gray-700">{error}</p>
          <button
            onClick={() => navigate('/dashboard')}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  // Cancel Subscription Modal
  const CancelModal = () => (
    showCancelModal && (
      <div className="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full flex items-center justify-center z-50">
        <div className="relative px-4 max-w-md w-full">
          <div className="bg-white rounded-lg shadow-xl p-6">
            <div className="flex justify-between items-start mb-4">
              <h3 className="text-lg font-medium text-gray-900">Cancel Subscription</h3>
              <button 
                onClick={() => setShowCancelModal(false)}
                className="text-gray-400 hover:text-gray-500"
              >
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            
            <div className="mb-4">
              <p className="text-gray-700 mb-3">
                Are you sure you want to cancel your {subscription?.plan_name} plan?
              </p>
              
              <div className="space-y-3">
                <div className="flex items-start">
                  <input
                    id="cancel-at-period-end"
                    name="cancel-type"
                    type="radio"
                    checked={!cancelImmediate}
                    onChange={() => setCancelImmediate(false)}
                    className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300"
                  />
                  <label htmlFor="cancel-at-period-end" className="ml-3 block text-sm text-gray-700">
                    <span className="font-medium">Cancel at period end</span>
                    <span className="text-gray-500 block">Your subscription will remain active until the end of the current billing period.</span>
                  </label>
                </div>
                
                <div className="flex items-start">
                  <input
                    id="cancel-immediate"
                    name="cancel-type"
                    type="radio"
                    checked={cancelImmediate}
                    onChange={() => setCancelImmediate(true)}
                    className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300"
                  />
                  <label htmlFor="cancel-immediate" className="ml-3 block text-sm text-gray-700">
                    <span className="font-medium">Cancel immediately</span>
                    <span className="text-gray-500 block">Your subscription will be canceled right away and you will lose access to premium features.</span>
                  </label>
                </div>
              </div>
            </div>
            
            <div className="flex space-x-3">
              <button
                onClick={() => setShowCancelModal(false)}
                className="flex-1 px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50"
              >
                Keep Subscription
              </button>
              <button
                onClick={() => handleCancelSubscription(cancelImmediate)}
                disabled={isCancelling}
                className="flex-1 px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-red-600 hover:bg-red-700 disabled:opacity-50"
              >
                {isCancelling ? 'Cancelling...' : 'Confirm Cancellation'}
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  );

  // Available plans
  // Use actual available plans from backend
  const plans = [
    {
      id: 'free',
      name: 'Free',
      price: '$0',
      priceDescription: 'per month',
      features: [
        'Up to 10 leads per day',
        'Basic lead scoring',
        'Email outreach templates',
        'Limited API access',
        'No AI features'
      ],
      current: !subscription || subscription.plan_name === 'free'
    },
    {
      id: 'pro',
      name: 'Pro',
      price: '$29',
      priceDescription: 'per month',
      features: [
        'Up to 500 leads per day',
        'Advanced lead scoring',
        'Custom outreach templates',
        'Priority support',
        'Full API access',
        'AI features included'
      ],
      current: subscription?.plan_name === 'pro'
    },
    {
      id: 'enterprise',
      name: 'Enterprise',
      price: '$99',
      priceDescription: 'per month',
      features: [
        'Up to 10,000 leads per day',
        'Advanced AI scoring',
        'Custom integrations',
        'Dedicated support',
        'Full API access',
        'Unlimited AI features'
      ],
      current: subscription?.plan_name === 'enterprise'
    }
  ];

  return (
    <div className="min-h-screen bg-gray-100">
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-bold text-gray-900">Billing & Subscription</h1>
        </div>
      </header>

      <main className="py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          {/* Current Subscription */}
          <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
            <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
              <h3 className="text-lg leading-6 font-medium text-gray-900">
                Current Subscription
              </h3>
              <p className="mt-1 text-sm text-gray-500">
                Manage your subscription and billing information
              </p>
            </div>
            <div className="px-4 py-5 sm:p-6">
              {subscription ? (
                <div className="space-y-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <h4 className="text-sm font-medium text-gray-900 mb-2">Plan Details</h4>
                      <div className="space-y-2">
                        <div>
                          <span className="text-sm font-medium text-gray-500">Plan:</span>
                          <p className="text-sm text-gray-900 capitalize">{subscription.plan_name}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Daily Lead Limit:</span>
                          <p className="text-sm text-gray-900">{subscription.max_leads_per_day}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">AI Features:</span>
                          <p className="text-sm text-gray-900">{subscription.can_use_ai ? 'Available' : 'Not Available'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Export Features:</span>
                          <p className="text-sm text-gray-900">{subscription.can_export ? 'Available' : 'Not Available'}</p>
                        </div>
                      </div>
                    </div>
                    
                    <div>
                      <h4 className="text-sm font-medium text-gray-900 mb-2">Actions</h4>
                      <div className="space-y-3">
                        <button
                          onClick={handleManageBilling}
                          disabled={billingPortalLoading}
                          className="w-full px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                        >
                          Manage Billing
                        </button>
                        
                        {subscription.plan_name !== 'enterprise' && (
                          <button
                            onClick={() => handleUpdatePlan('enterprise')}
                            className="w-full px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700"
                          >
                            Upgrade to Enterprise
                          </button>
                        )}
                        
                        {subscription.plan_name !== 'pro' && subscription.plan_name !== 'enterprise' && (
                          <button
                            onClick={() => handleUpdatePlan('pro')}
                            className="w-full px-4 py-2 bg-yellow-600 text-white rounded-md hover:bg-yellow-700"
                          >
                            Upgrade to Pro
                          </button>
                        )}
                        
                        {subscription.plan_name !== 'free' && (
                          <button
                            onClick={() => setShowCancelModal(true)}
                            className="w-full px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700"
                          >
                            Cancel Subscription
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-center py-8">
                  <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <h3 className="mt-2 text-sm font-medium text-gray-900">No subscription data</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    Your subscription information will appear here.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Pricing Plans */}
          {/* Plan selection is not needed as free plan is automatic */}
          <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
            <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
              <h3 className="text-lg leading-6 font-medium text-gray-900">
                Subscription Plans
              </h3>
              <p className="mt-1 text-sm text-gray-500">
                Choose a plan that fits your needs
              </p>
            </div>
            <div className="px-4 py-5 sm:p-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                {plans.map((plan) => (
                  <div
                    key={plan.id}
                    className={`border rounded-lg p-6 ${
                      plan.current ? 'border-blue-500 bg-blue-50' : 'border-gray-200'
                    }`}
                  >
                    <h4 className="text-lg font-medium text-gray-900">{plan.name}</h4>
                    <div className="mt-4">
                      <p className="text-4xl font-extrabold text-gray-900">{plan.price}</p>
                      <p className="text-base text-gray-500">{plan.priceDescription}</p>
                    </div>
                    <ul className="mt-6 space-y-4">
                      {plan.features.map((feature, index) => (
                        <li key={index} className="flex items-start">
                          <svg className="h-6 w-6 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          <span className="ml-3 text-base text-gray-700">{feature}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="mt-8">
                      {plan.current ? (
                        <span className="inline-flex items-center px-4 py-2 border border-transparent text-base font-medium rounded-md shadow-sm text-white bg-blue-600">
                          Current Plan
                        </span>
                      ) : (
                        <button
                          onClick={() => handleUpdatePlan(plan.id)}
                          className={`w-full inline-flex items-center justify-center px-6 py-3 border border-transparent text-base font-medium rounded-md shadow-sm text-white ${
                            'bg-blue-600 hover:bg-blue-700'
                          }`}
                        >
                          Upgrade to {plan.name}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Usage Records */}
          <div className="bg-white shadow overflow-hidden sm:rounded-lg">
            <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
              <h3 className="text-lg leading-6 font-medium text-gray-900">
                Usage Records
              </h3>
              <p className="mt-1 text-sm text-gray-500">
                Track your usage for metered billing
              </p>
            </div>
            <div className="px-4 py-5 sm:p-6">
              <div className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="border rounded-lg p-4 bg-blue-50">
                    <h4 className="text-sm font-medium text-gray-900">Current Plan</h4>
                    <p className="text-lg font-semibold text-gray-900 capitalize">{subscription?.plan_name || 'free'}</p>
                  </div>
                  <div className="border rounded-lg p-4 bg-green-50">
                    <h4 className="text-sm font-medium text-gray-900">Daily Limit</h4>
                    <p className="text-lg font-semibold text-gray-900">{subscription?.max_leads_per_day || 10}</p>
                  </div>
                  <div className="border rounded-lg p-4 bg-yellow-50">
                    <h4 className="text-sm font-medium text-gray-900">Used Today</h4>
                    <p className="text-lg font-semibold text-gray-900">{subscription?.current_usage || 0}</p>
                  </div>
                </div>
                
                <div className="mt-6">
                  <h4 className="text-sm font-medium text-gray-900 mb-2">Plan Features</h4>
                  <ul className="space-y-2">
                    <li className="flex items-center">
                      <svg className="h-5 w-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="ml-2 text-sm text-gray-700">AI Features: {subscription?.can_use_ai ? 'Available' : 'Not Available'}</span>
                    </li>
                    <li className="flex items-center">
                      <svg className="h-5 w-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="ml-2 text-sm text-gray-700">Export Data: {subscription?.can_export ? 'Available' : 'Not Available'}</span>
                    </li>
                    <li className="flex items-center">
                      <svg className="h-5 w-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="ml-2 text-sm text-gray-700">Remaining Daily Leads: {subscription?.remaining_daily_leads || 10}</span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
      <CancelModal />
    </div>
  );
}