// src/components/LoadingState.jsx

export default function LoadingState({
  loading,
  error,
  isEmpty,
  emptyMessage = "No results found.",
}) {
  if (loading) {
    return (
      <div className="flex justify-center items-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-500 border-t-transparent"></div>
        <span className="ml-3 text-gray-600">Processing leads...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-800 p-4 rounded-md">
        <strong>Error:</strong> {error}
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="bg-gray-50 border border-gray-200 text-gray-600 p-4 rounded-md text-center">
        {emptyMessage}
      </div>
    );
  }

  return null;
}
