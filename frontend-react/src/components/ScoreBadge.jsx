// src/components/ScoreBadge.jsx

export default function ScoreBadge({ score = 0, label }) {
  let colorClasses = "bg-gray-100 text-gray-700 border-gray-300";

  if (score >= 80) {
    colorClasses = "bg-green-100 text-green-800 border-green-300";
  } else if (score >= 60) {
    colorClasses = "bg-blue-100 text-blue-800 border-blue-300";
  } else if (score >= 40) {
    colorClasses = "bg-yellow-100 text-yellow-800 border-yellow-300";
  } else {
    colorClasses = "bg-red-100 text-red-800 border-red-300";
  }

  return (
    <div
      className={`inline-flex items-center px-3 py-1 rounded-full border text-sm font-medium ${colorClasses}`}
      title={`Lead score: ${score}`}
    >
      <span className="mr-2">Score:</span>
      <span className="font-semibold">{score}</span>
      {label && (
        <span className="ml-2 text-xs opacity-80">({label})</span>
      )}
    </div>
  );
}
