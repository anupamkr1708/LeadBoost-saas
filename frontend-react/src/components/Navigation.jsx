// src/components/Navigation.jsx
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Navigation() {
  const { user, isAuthenticated, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <nav className="bg-white border-b">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex items-center">
            <div className="text-lg font-semibold text-gray-900">
              <Link to={isAuthenticated ? '/dashboard' : '/'}>
                Lead Intelligence Platform
              </Link>
            </div>
            {isAuthenticated && (
              <div className="hidden md:ml-6 md:flex md:space-x-8">
                <Link
                  to="/dashboard"
                  className="border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 border-b-2 py-2 px-1 text-sm font-medium"
                >
                  Dashboard
                </Link>
                <Link
                  to="/finder"
                  className="border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 border-b-2 py-2 px-1 text-sm font-medium"
                >
                  Lead Finder
                </Link>
                <Link
                  to="/leads"
                  className="border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 border-b-2 py-2 px-1 text-sm font-medium"
                >
                  All Leads
                </Link>
                <Link
                  to="/billing"
                  className="border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 border-b-2 py-2 px-1 text-sm font-medium"
                >
                  Billing
                </Link>
                <Link
                  to="/profile"
                  className="border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700 border-b-2 py-2 px-1 text-sm font-medium"
                >
                  Profile
                </Link>
              </div>
            )}
          </div>
          
          <div className="flex items-center">
            {isAuthenticated ? (
              <div className="flex items-center space-x-4">
                <div className="text-sm text-gray-700">
                  Welcome, {user?.first_name || user?.email}
                </div>
                <div className="relative">
                  <button
                    id="user-menu-button"
                    type="button"
                    className="flex text-sm rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                    aria-expanded="false"
                    aria-haspopup="true"
                  >
                    <span className="sr-only">Open user menu</span>
                    <div className="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-800 font-medium">
                      {user?.first_name?.charAt(0) || user?.email?.charAt(0) || 'U'}
                    </div>
                  </button>
                </div>
                <button
                  onClick={handleLogout}
                  className="ml-4 px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
                >
                  Logout
                </button>
              </div>
            ) : (
              <div className="flex space-x-4">
                <Link
                  to="/login"
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
                >
                  Sign In
                </Link>
                <Link
                  to="/register"
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200"
                >
                  Sign Up
                </Link>
              </div>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
