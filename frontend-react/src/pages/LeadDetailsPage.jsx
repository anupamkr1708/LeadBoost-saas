// src/pages/LeadDetailsPage.jsx
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getLead, updateLead, deleteLead, processLeadNow } from '../api/client';
import ScoreBadge from '../components/ScoreBadge';
import { format } from 'date-fns';

export default function LeadDetailsPage() {
  const { leadId } = useParams();
  const navigate = useNavigate();
  const [lead, setLead] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [editData, setEditData] = useState({});
  const [processing, setProcessing] = useState(false);

  useEffect(() => {
    fetchLead();
  }, [leadId]);

  const fetchLead = async () => {
    try {
      setLoading(true);
      const data = await getLead(parseInt(leadId));
      setLead(data);
      setEditData({
        company_name: data.company_name || '',
        industry: data.industry || '',
        about_text: data.about_text || '',
        contact_name: data.contact_name || '',
        contact_title: data.contact_title || '',
        email: data.email || '',
        phone: data.phone || '',
        address: data.address || '',
        linkedin_url: data.linkedin_url || '',
        twitter_url: data.twitter_url || '',
        facebook_url: data.facebook_url || '',
        employees: data.employees || '',
        revenue_band: data.revenue_band || '',
        founded_year: data.founded_year || null,
        outreach_message: data.outreach_message || '',
      });
    } catch (err) {
      setError('Failed to load lead details: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleEditToggle = () => {
    if (isEditing) {
      // Cancel edit
      setEditData({
        company_name: lead.company_name || '',
        industry: lead.industry || '',
        about_text: lead.about_text || '',
        contact_name: lead.contact_name || '',
        contact_title: lead.contact_title || '',
        email: lead.email || '',
        phone: lead.phone || '',
        address: lead.address || '',
        linkedin_url: lead.linkedin_url || '',
        twitter_url: lead.twitter_url || '',
        facebook_url: lead.facebook_url || '',
        employees: lead.employees || '',
        revenue_band: lead.revenue_band || '',
        founded_year: lead.founded_year || null,
        outreach_message: lead.outreach_message || '',
      });
    }
    setIsEditing(!isEditing);
  };

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setEditData(prev => ({
      ...prev,
      [name]: value
    }));
  };

  const handleSave = async () => {
    try {
      const updatedLead = await updateLead(parseInt(leadId), editData);
      setLead(updatedLead);
      setIsEditing(false);
      // Show success message or notification
    } catch (err) {
      setError('Failed to update lead: ' + err.message);
    }
  };

  const handleDelete = async () => {
    if (window.confirm('Are you sure you want to delete this lead?')) {
      try {
        await deleteLead(parseInt(leadId));
        navigate('/leads');
      } catch (err) {
        setError('Failed to delete lead: ' + err.message);
      }
    }
  };

  const handleProcessNow = async () => {
    setProcessing(true);
    try {
      await processLeadNow(parseInt(leadId));
      // Refresh the lead data after processing
      await fetchLead();
    } catch (err) {
      setError('Failed to process lead: ' + err.message);
    } finally {
      setProcessing(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
          <p className="mt-2 text-gray-600">Loading lead details...</p>
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
            onClick={() => navigate('/leads')}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            Back to Leads
          </button>
        </div>
      </div>
    );
  }

  if (!lead) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <div className="max-w-md w-full bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Lead Not Found</h2>
          <p className="text-gray-700">The requested lead could not be found.</p>
          <button
            onClick={() => navigate('/leads')}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            Back to Leads
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-100">
      <header className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center">
            <h1 className="text-3xl font-bold text-gray-900">Lead Details</h1>
            <div className="flex space-x-3">
              <button
                onClick={handleProcessNow}
                disabled={processing}
                className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
              >
                {processing ? 'Processing...' : 'Process Now'}
              </button>
              <button
                onClick={handleEditToggle}
                className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
              >
                {isEditing ? 'Cancel' : 'Edit'}
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="py-6">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          {isEditing ? (
            <div className="bg-white shadow overflow-hidden sm:rounded-lg p-6">
              <h2 className="text-xl font-bold text-gray-900 mb-6">Edit Lead</h2>
              <div className="grid grid-cols-1 gap-6">
                <div>
                  <label className="block text-sm font-medium text-gray-700">Website</label>
                  <input
                    type="text"
                    value={lead.website}
                    disabled
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm bg-gray-100"
                  />
                </div>
                
                <div>
                  <label htmlFor="company_name" className="block text-sm font-medium text-gray-700">Company Name</label>
                  <input
                    type="text"
                    name="company_name"
                    value={editData.company_name}
                    onChange={handleInputChange}
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                
                <div>
                  <label htmlFor="industry" className="block text-sm font-medium text-gray-700">Industry</label>
                  <input
                    type="text"
                    name="industry"
                    value={editData.industry}
                    onChange={handleInputChange}
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                
                <div>
                  <label htmlFor="about_text" className="block text-sm font-medium text-gray-700">About</label>
                  <textarea
                    name="about_text"
                    value={editData.about_text}
                    onChange={handleInputChange}
                    rows={3}
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label htmlFor="contact_name" className="block text-sm font-medium text-gray-700">Contact Name</label>
                    <input
                      type="text"
                      name="contact_name"
                      value={editData.contact_name}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="contact_title" className="block text-sm font-medium text-gray-700">Contact Title</label>
                    <input
                      type="text"
                      name="contact_title"
                      value={editData.contact_title}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label htmlFor="email" className="block text-sm font-medium text-gray-700">Email</label>
                    <input
                      type="email"
                      name="email"
                      value={editData.email}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="phone" className="block text-sm font-medium text-gray-700">Phone</label>
                    <input
                      type="text"
                      name="phone"
                      value={editData.phone}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                </div>
                
                <div>
                  <label htmlFor="address" className="block text-sm font-medium text-gray-700">Address</label>
                  <input
                    type="text"
                    name="address"
                    value={editData.address}
                    onChange={handleInputChange}
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div>
                    <label htmlFor="linkedin_url" className="block text-sm font-medium text-gray-700">LinkedIn URL</label>
                    <input
                      type="url"
                      name="linkedin_url"
                      value={editData.linkedin_url}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="twitter_url" className="block text-sm font-medium text-gray-700">Twitter URL</label>
                    <input
                      type="url"
                      name="twitter_url"
                      value={editData.twitter_url}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="facebook_url" className="block text-sm font-medium text-gray-700">Facebook URL</label>
                    <input
                      type="url"
                      name="facebook_url"
                      value={editData.facebook_url}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div>
                    <label htmlFor="employees" className="block text-sm font-medium text-gray-700">Employees</label>
                    <input
                      type="text"
                      name="employees"
                      value={editData.employees}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="revenue_band" className="block text-sm font-medium text-gray-700">Revenue Band</label>
                    <input
                      type="text"
                      name="revenue_band"
                      value={editData.revenue_band}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label htmlFor="founded_year" className="block text-sm font-medium text-gray-700">Founded Year</label>
                    <input
                      type="number"
                      name="founded_year"
                      value={editData.founded_year || ''}
                      onChange={handleInputChange}
                      className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                </div>
                
                <div>
                  <label htmlFor="outreach_message" className="block text-sm font-medium text-gray-700">Outreach Message</label>
                  <textarea
                    name="outreach_message"
                    value={editData.outreach_message}
                    onChange={handleInputChange}
                    rows={4}
                    className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
              </div>
              
              <div className="mt-6 flex justify-end space-x-3">
                <button
                  onClick={handleEditToggle}
                  className="px-4 py-2 bg-gray-300 text-gray-700 rounded-md hover:bg-gray-400"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
                >
                  Save Changes
                </button>
              </div>
            </div>
          ) : (
            <div className="bg-white shadow overflow-hidden sm:rounded-lg">
              <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="text-lg leading-6 font-medium text-gray-900">
                      {lead.company_name || 'Unknown Company'}
                    </h3>
                    <p className="mt-1 text-sm text-gray-500">
                      {lead.website}
                    </p>
                  </div>
                  <div>
                    <ScoreBadge score={lead.score} label={lead.qualification_label} />
                  </div>
                </div>
              </div>
              
              <div className="px-4 py-5 sm:p-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <h4 className="text-sm font-medium text-gray-900 mb-2">Basic Information</h4>
                    <div className="space-y-2">
                      <div>
                        <span className="text-sm font-medium text-gray-500">Website:</span>
                        <p className="text-sm text-gray-900">{lead.website}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">Company Name:</span>
                        <p className="text-sm text-gray-900">{lead.company_name || 'N/A'}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">Industry:</span>
                        <p className="text-sm text-gray-900">{lead.industry || 'N/A'}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">About:</span>
                        <p className="text-sm text-gray-900">{lead.about_text || 'N/A'}</p>
                      </div>
                    </div>
                  </div>
                  
                  <div>
                    <h4 className="text-sm font-medium text-gray-900 mb-2">Contact Information</h4>
                    <div className="space-y-2">
                      <div>
                        <span className="text-sm font-medium text-gray-500">Contact Name:</span>
                        <p className="text-sm text-gray-900">{lead.contact_name || 'N/A'}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">Contact Title:</span>
                        <p className="text-sm text-gray-900">{lead.contact_title || 'N/A'}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">Email:</span>
                        <p className="text-sm text-gray-900">{lead.email || 'N/A'}</p>
                      </div>
                      <div>
                        <span className="text-sm font-medium text-gray-500">Phone:</span>
                        <p className="text-sm text-gray-900">{lead.phone || 'N/A'}</p>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div className="mt-6">
                  <h4 className="text-sm font-medium text-gray-900 mb-2">Company Details</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <div className="space-y-2">
                        <div>
                          <span className="text-sm font-medium text-gray-500">Address:</span>
                          <p className="text-sm text-gray-900">{lead.address || 'N/A'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Employees:</span>
                          <p className="text-sm text-gray-900">{lead.employees || 'N/A'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Revenue Band:</span>
                          <p className="text-sm text-gray-900">{lead.revenue_band || 'N/A'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Founded Year:</span>
                          <p className="text-sm text-gray-900">{lead.founded_year || 'N/A'}</p>
                        </div>
                      </div>
                    </div>
                    
                    <div>
                      <div className="space-y-2">
                        <div>
                          <span className="text-sm font-medium text-gray-500">LinkedIn:</span>
                          <p className="text-sm text-gray-900">
                            {lead.linkedin_url ? (
                              <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                                {lead.linkedin_url}
                              </a>
                            ) : 'N/A'}
                          </p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Twitter:</span>
                          <p className="text-sm text-gray-900">
                            {lead.twitter_url ? (
                              <a href={lead.twitter_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                                {lead.twitter_url}
                              </a>
                            ) : 'N/A'}
                          </p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Facebook:</span>
                          <p className="text-sm text-gray-900">
                            {lead.facebook_url ? (
                              <a href={lead.facebook_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                                {lead.facebook_url}
                              </a>
                            ) : 'N/A'}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div className="mt-6">
                  <h4 className="text-sm font-medium text-gray-900 mb-2">Scoring & Enrichment</h4>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div>
                      <span className="text-sm font-medium text-gray-500">Score:</span>
                      <p className="text-sm text-gray-900">{lead.score}</p>
                    </div>
                    <div>
                      <span className="text-sm font-medium text-gray-500">Qualification Label:</span>
                      <p className="text-sm text-gray-900">{lead.qualification_label}</p>
                    </div>
                    <div>
                      <span className="text-sm font-medium text-gray-500">Confidence:</span>
                      <p className="text-sm text-gray-900">
                        Scrape: {lead.scrape_confidence}, 
                        Email: {lead.email_confidence}, 
                        Enrichment: {lead.enrichment_confidence}
                      </p>
                    </div>
                  </div>
                </div>
                
                <div className="mt-6">
                  <h4 className="text-sm font-medium text-gray-900 mb-2">Outreach</h4>
                  <div>
                    <span className="text-sm font-medium text-gray-500">Outreach Message:</span>
                    <pre className="mt-1 bg-gray-50 border rounded p-3 text-sm text-gray-800 whitespace-pre-wrap">
                      {lead.outreach_message || 'No outreach message generated'}
                    </pre>
                  </div>
                </div>
                
                <div className="mt-6">
                  <h4 className="text-sm font-medium text-gray-900 mb-2">Metadata</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <div className="space-y-2">
                        <div>
                          <span className="text-sm font-medium text-gray-500">Created:</span>
                          <p className="text-sm text-gray-900">{lead.created_at ? format(new Date(lead.created_at), 'MMM dd, yyyy HH:mm') : 'N/A'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Updated:</span>
                          <p className="text-sm text-gray-900">{lead.updated_at ? format(new Date(lead.updated_at), 'MMM dd, yyyy HH:mm') : 'N/A'}</p>
                        </div>
                      </div>
                    </div>
                    <div>
                      <div className="space-y-2">
                        <div>
                          <span className="text-sm font-medium text-gray-500">Active:</span>
                          <p className="text-sm text-gray-900">{lead.is_active ? 'Yes' : 'No'}</p>
                        </div>
                        <div>
                          <span className="text-sm font-medium text-gray-500">Verified:</span>
                          <p className="text-sm text-gray-900">{lead.is_verified ? 'Yes' : 'No'}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}