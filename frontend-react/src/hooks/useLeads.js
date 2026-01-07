// src/hooks/useLeads.js

import { useState, useEffect } from "react";
import { processLeads, getLeads, deleteLead, updateLead, processLeadNow } from "../api/client";

/**
 * Custom hook to manage lead processing lifecycle
 */
export function useLeads() {
  const [leads, setLeads] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [singleLeadLoading, setSingleLeadLoading] = useState({});

  // Load leads on mount
  useEffect(() => {
    fetchLeads();
  }, []);

  const fetchLeads = async (skip = 0, limit = 100) => {
    setLoading(true);
    setError(null);

    try {
      const response = await getLeads(skip, limit);
      setLeads(Array.isArray(response) ? response : []);
    } catch (err) {
      setError(err.message || "Something went wrong");
      setLeads([]);
    } finally {
      setLoading(false);
    }
  };

  /**
   * Submit URLs to backend
   */
  const runLeadProcessing = async (urls, messageStyle = "professional") => {
    setLoading(true);
    setError(null);

    try {
      const response = await processLeads(urls, messageStyle);

      // Defensive: ensure array
      setLeads(prevLeads => [...response, ...prevLeads]);
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  /**
   * Delete a lead
   */
  const removeLead = async (leadId) => {
    try {
      await deleteLead(leadId);
      setLeads(prevLeads => prevLeads.filter(lead => lead.id !== leadId));
      return { success: true };
    } catch (err) {
      setError(err.message || "Failed to delete lead");
      return { success: false, error: err.message };
    }
  };

  /**
   * Update a lead
   */
  const updateLeadData = async (leadId, leadData) => {
    try {
      const updatedLead = await updateLead(leadId, leadData);
      setLeads(prevLeads => 
        prevLeads.map(lead => 
          lead.id === leadId ? updatedLead : lead
        )
      );
      return { success: true, data: updatedLead };
    } catch (err) {
      setError(err.message || "Failed to update lead");
      return { success: false, error: err.message };
    }
  };

  /**
   * Process a single lead now
   */
  const processSingleLead = async (leadId) => {
    setSingleLeadLoading(prev => ({ ...prev, [leadId]: true }));
    
    try {
      await processLeadNow(leadId);
      // Refresh the specific lead after processing
      // In a real implementation, you might want to poll for updates
      return { success: true };
    } catch (err) {
      setError(err.message || "Failed to process lead");
      return { success: false, error: err.message };
    } finally {
      setSingleLeadLoading(prev => ({ ...prev, [leadId]: false }));
    }
  };

  /**
   * Reset state (optional)
   */
  const resetLeads = () => {
    setLeads([]);
    setError(null);
  };

  return {
    leads,
    loading,
    error,
    singleLeadLoading,
    runLeadProcessing,
    fetchLeads,
    removeLead,
    updateLeadData,
    processSingleLead,
    resetLeads,
  };
}
