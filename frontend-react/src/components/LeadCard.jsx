// src/components/LeadCard.jsx

import ScoreBadge from "./ScoreBadge";

export default function LeadCard({ lead }) {
  if (!lead) return null;

  const {
    website,
    industry,
    employees,
    revenue_band,
    score,
    qualification_label,
    outreach_message,
    message_style,
    email,
    email_confidence,
  } = lead;

  return (
    <div className="border rounded-lg shadow-sm p-5 bg-white space-y-4">
      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">
            {website || "Unknown Website"}
          </h3>

          <p className="text-sm text-gray-500">
            {industry || "Unknown Industry"} •{" "}
            {employees || "Unknown Size"} •{" "}
            {revenue_band || "Unknown Revenue"}
          </p>
        </div>

        <ScoreBadge score={score} label={qualification_label} />
      </div>

      {/* Email */}
      <div className="text-sm text-gray-700">
        <span className="font-medium">Email:</span>{" "}
        {email ? (
          <>
            {email}
            <span className="ml-2 text-xs text-gray-500">
              (confidence: {email_confidence})
            </span>
          </>
        ) : (
          <span className="italic text-gray-400">Not found</span>
        )}
      </div>

      {/* Outreach Message */}
      <div>
        <h4 className="text-sm font-medium text-gray-800 mb-1">
          Outreach Message{" "}
          <span className="text-xs text-gray-500">
            ({message_style})
          </span>
        </h4>

        <pre className="bg-gray-50 border rounded p-3 text-sm text-gray-800 whitespace-pre-wrap">
          {outreach_message || "No message generated"}
        </pre>
      </div>
    </div>
  );
}
