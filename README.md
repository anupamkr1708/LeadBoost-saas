# LeadBoost SaaS - Lead Intelligence Platform

LeadBoost AI is a full-stack, multi-tenant, subscription-based AI SaaS platform for automated lead discovery, enrichment, scoring, and outreach generation.


## Table of Contents
- [Overview](#overview)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Features](#features)
- [Subscription System](#subscription-system)
- [Backend Architecture](#backend-architecture)
- [Frontend Architecture](#frontend-architecture)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [API Endpoints](#api-endpoints)
- [Benefits](#benefits)
- [Development Guidelines](#development-guidelines)

## Overview

LeadBoost SaaS is a production-grade lead intelligence platform that enables businesses to discover, enrich, and engage with potential leads. The platform combines web scraping, AI-powered data enrichment, and intelligent outreach message generation to help sales teams identify and connect with high-quality prospects.

At its core, LeadBoost SaaS provides:

• Automated company data extraction using modern scraping and browser automation techniques
• AI-assisted enrichment for industry, size, and revenue inference
• Deterministic lead scoring and qualification engine
• Email discovery and validation workflows
• Personalized outreach message generation
• Multi-tenant SaaS architecture with organizations, users, API keys, and subscription models
• Billing-ready backend with Stripe-style subscription flows
• Secure authentication, domain models, and infrastructure-level separation
• Frontend dashboard for lead management, scoring visualization, and outreach workflows

The backend is built using FastAPI and follows a clean architecture inspired structure, separating domain logic, infrastructure services, API delivery, and background workers. This makes the platform extensible, testable, and ready for scaling into microservices if needed.

The frontend is built with React, Vite, and Tailwind CSS, providing a responsive, SaaS-style dashboard experience.

The system features a multi-tier subscription model, PostgreSQL database integration, Celery-based background processing, and a modern React frontend with real-time lead tracking.

LeadBoost SaaS is designed not as a demo project, but as a real product foundation that can be deployed, extended, monetized, and sold to businesses seeking AI-powered lead intelligence and automation.


## Technology Stack

### Backend
- **Framework**: FastAPI - High-performance Python web framework with automatic API documentation
- **Database**: PostgreSQL - Production-grade relational database with support for complex queries
- **ORM**: SQLAlchemy - Python SQL toolkit and Object-Relational Mapping
- **Authentication**: JWT tokens with refresh token support
- **Background Processing**: Celery with Redis as message broker
- **Caching**: Redis - In-memory data structure store
- **Web Scraping**: Playwright for dynamic content and requests for static content
- **AI Integration**: LangChain with Groq API for LLM-powered data enrichment
- **Security**: bcrypt for password hashing, python-jose for JWT handling
- **API Documentation**: Automatic OpenAPI/Swagger UI generation

### Frontend
- **Framework**: React 18 with Vite - Fast build tooling and HMR
- **State Management**: React Context API and custom hooks
- **Routing**: React Router v6
- **Styling**: Tailwind CSS - Utility-first CSS framework
- **HTTP Client**: Axios with interceptors for API communication
- **Build Tool**: Vite - Next-generation frontend tooling

### Infrastructure
- **Containerization**: Docker and Docker Compose for container orchestration
- **Environment Management**: Python virtual environments and .env files
- **Task Queue**: Celery with Redis backend for asynchronous processing
- **Database Migrations**: Alembic for database schema management

## Architecture

### Backend Architecture

```
├── api/
│   └── endpoints/
│       ├── auth.py          # Authentication endpoints (register, login, refresh)
│       ├── billing.py       # Subscription and usage endpoints
│       ├── leads.py         # Lead management endpoints
│       └── organizations.py # Organization management endpoints
├── core/
│   ├── domain/
│   │   ├── models/          # SQLAlchemy ORM models
│   │   │   ├── user.py
│   │   │   ├── organization.py
│   │   │   ├── lead.py
│   │   │   ├── subscription.py
│   │   │   └── billing.py
│   │   ├── schemas/         # Pydantic validation schemas
│   │   │   ├── user.py
│   │   │   ├── organization.py
│   │   │   ├── lead.py
│   │   │   ├── subscription.py
│   │   │   └── billing.py
│   │   └── services/        # Business logic services
│   └── infrastructure/
│       ├── auth/            # Authentication utilities
│       ├── billing/         # Subscription management
│       ├── database/        # Database setup and CRUD operations
│       ├── enrichment/      # AI-powered data enrichment
│       ├── logging/         # Structured logging
│       ├── messaging/       # Outreach message generation
│       ├── scraping/        # Web scraping utilities
│       └── workers/         # Celery task orchestrator
├── main.py                  # FastAPI application entry point
└── requirements.txt         # Python dependencies
```

### Frontend Architecture

```
├── src/
│   ├── api/                 # API client and HTTP utilities
│   │   ├── auth.js
│   │   └── client.js
│   ├── components/          # Reusable UI components
│   │   ├── Layout.jsx
│   │   ├── LeadCard.jsx
│   │   ├── LoadingState.jsx
│   │   ├── Navigation.jsx
│   │   └── ScoreBadge.jsx
│   ├── context/             # React Context providers
│   │   └── AuthContext.jsx
│   ├── hooks/               # Custom React hooks
│   │   └── useLeads.js
│   ├── pages/               # Route-level components
│   │   ├── auth/
│   │   │   ├── LoginPage.jsx
│   │   │   └── RegisterPage.jsx
│   │   ├── BillingPage.jsx
│   │   ├── Dashboard.jsx
│   │   ├── FinderPage.jsx
│   │   ├── LeadDetailsPage.jsx
│   │   ├── LeadsPage.jsx
│   │   ├── OrganizationPage.jsx
│   │   └── ProfilePage.jsx
│   ├── App.jsx              # Main application component
│   ├── index.css            # Global styles
│   └── main.jsx             # Application entry point
├── public/                  # Static assets
├── package.json             # Node.js dependencies
└── vite.config.js           # Vite build configuration
```

## Features

### Core Features
- **Lead Discovery**: Process company URLs to discover new leads
- **Data Enrichment**: AI-powered enrichment of company and contact information
- **Lead Scoring**: Intelligent scoring based on company size, industry, and other factors
- **Outreach Generation**: AI-generated personalized outreach messages
- **Multi-tenancy**: Organization-based data isolation
- **Real-time Processing**: Background task processing with progress tracking

### Authentication & Authorization
- **JWT-based Authentication**: Secure token-based authentication
- **Role-based Access Control**: Organization-level access control
- **Password Security**: Bcrypt password hashing with 72-character limit
- **Session Management**: Automatic token refresh

### Subscription System
- **Tiered Plans**: Free, Pro, and Enterprise tiers
- **Usage Tracking**: Daily lead limits with real-time tracking
- **Feature Gates**: AI features, export capabilities, and lead limits based on plan
- **Plan Management**: Upgrade/downgrade functionality

### Data Processing
- **Web Scraping**: Multi-layered scraping approach (Playwright + requests)
- **AI Integration**: LangChain with Groq API for intelligent data processing
- **Background Processing**: Asynchronous lead processing with Celery
- **Confidence Scoring**: Quality assessment for scraped and enriched data

## Subscription System

### Plan Tiers

| Feature | Free Plan | Pro Plan | Enterprise Plan |
|---------|-----------|----------|-----------------|
| Daily Lead Limit | 10 leads/day | 500 leads/day | 10,000 leads/day |
| AI Features | ❌ | ✅ | ✅ |
| Export Data | ❌ | ✅ | ✅ |
| Lead Scoring | Basic | Advanced | Advanced |
| Support | Community | Priority | Dedicated |
| API Access | Limited | Full | Full |

### Implementation Details
- **Database Integration**: PostgreSQL tables for plans, subscriptions, and usage records
- **Real-time Limits**: Daily usage tracking with automatic enforcement
- **Flexible Upgrades**: Instant plan changes with prorated billing
- **Feature Flags**: Dynamic feature availability based on subscription tier

## Backend Architecture

### Database Models
- **User**: Authentication and authorization
- **Organization**: Multi-tenancy and data isolation
- **Lead**: Lead data with enrichment and scoring
- **Subscription**: Plan and billing information
- **UsageRecord**: Daily usage tracking
- **Plan**: Subscription tier definitions

### API Endpoints

#### Authentication
- `POST /api/v2/register` - User registration
- `POST /api/v2/login` - User login
- `POST /api/v2/refresh` - Token refresh
- `GET /api/v2/me` - Get current user
- `PUT /api/v2/me` - Update current user

#### Lead Management
- `POST /api/v2/leads/` - Create leads from URLs
- `POST /api/v2/leads/single` - Create single lead
- `GET /api/v2/leads/` - Get user's leads
- `GET /api/v2/leads/{lead_id}` - Get specific lead
- `PUT /api/v2/leads/{lead_id}` - Update lead
- `DELETE /api/v2/leads/{lead_id}` - Delete lead
- `POST /api/v2/leads/{lead_id}/process` - Process lead now

#### Organizations
- `GET /api/v2/organizations` - Get user's organization
- `GET /api/v2/organizations/{org_id}` - Get specific organization
- `PUT /api/v2/organizations/{org_id}` - Update organization

#### Billing & Subscription
- `GET /api/v2/plans` - Get available subscription plans
- `GET /api/v2/usage` - Get current usage and plan details
- `POST /api/v2/upgrade` - Upgrade subscription plan

### Background Processing
- **Task Queue**: Celery with Redis backend
- **Processing Pipeline**: Scraping → Enrichment → Scoring → Outreach Generation
- **Error Handling**: Graceful degradation with fallback mechanisms
- **Progress Tracking**: Real-time status updates

## Frontend Architecture

### State Management
- **Auth Context**: User authentication state management
- **Lead Hooks**: Custom hook for lead processing lifecycle
- **Global State**: Minimal global state with component-level state where appropriate

### API Integration
- **Axios Client**: Centralized HTTP client with interceptors
- **Error Handling**: Automatic error handling and user feedback
- **Loading States**: Comprehensive loading and error states
- **Token Management**: Automatic JWT token refresh

### Components
- **Reusable Components**: Navigation, Layout, LoadingState, ScoreBadge
- **Page Components**: Dashboard, Finder, Leads, Billing, Profile
- **Form Components**: Login, Registration with validation
- **Data Display**: Lead cards with rich information display

## Installation & Setup

### Prerequisites
- Python 3.9+
- Node.js 18+
- PostgreSQL 12+
- Redis 6+
- Docker (optional but recommended)

### Backend Setup
```bash
# Navigate to backend directory
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration

# Run database migrations
python -c "from core.infrastructure.database import init_db; init_db()"

# Start the application
python -m uvicorn main:app --reload
```

### Frontend Setup
```bash
# Navigate to frontend directory
cd frontend-react

# Install dependencies
npm install

# Set up environment variables
cp .env.example .env
# Edit .env with your API configuration

# Start development server
npm run dev
```

### Docker Setup (Recommended)
```bash
# Build and start all services
docker-compose up --build

# Or in detached mode
docker-compose up --build -d
```

## Usage

### Getting Started
1. Register a new account through the frontend
2. Log in to access the dashboard
3. Navigate to the Lead Finder page
4. Enter company URLs to process
5. Monitor lead processing progress
6. View enriched leads with scores and outreach messages

### Subscription Management
1. Navigate to the Billing page
2. View current plan details and usage
3. Upgrade to a higher tier as needed
4. Monitor daily usage limits

### Lead Processing Workflow
1. User submits company URLs
2. Backend creates lead records in database
3. Background tasks scrape company websites
4. AI enriches company and contact data
5. Lead scoring algorithm evaluates prospects
6. Outreach message generator creates personalized messages
7. Results are displayed in the UI

## API Endpoints

### Authentication Endpoints
- `POST /api/v2/register` - Register new user
- `POST /api/v2/login` - Login and get tokens
- `POST /api/v2/refresh` - Refresh access token
- `GET /api/v2/me` - Get current user details
- `PUT /api/v2/me` - Update current user

### Lead Endpoints
- `POST /api/v2/leads/` - Process multiple URLs
- `POST /api/v2/leads/single` - Create single lead
- `GET /api/v2/leads/` - Get user's leads
- `GET /api/v2/leads/{id}` - Get specific lead
- `PUT /api/v2/leads/{id}` - Update lead
- `DELETE /api/v2/leads/{id}` - Delete lead
- `POST /api/v2/leads/{id}/process` - Process lead now

### Organization Endpoints
- `GET /api/v2/organizations` - Get user's organization
- `GET /api/v2/organizations/{id}` - Get specific organization
- `PUT /api/v2/organizations/{id}` - Update organization

### Billing Endpoints
- `GET /api/v2/plans` - Get available plans
- `GET /api/v2/usage` - Get usage details
- `POST /api/v2/upgrade` - Upgrade subscription

## Benefits

### For Businesses
- **Increased Efficiency**: Automated lead discovery and enrichment
- **Higher Quality Leads**: AI-powered scoring and qualification
- **Time Savings**: Reduce manual research by 80%
- **Scalability**: Handle thousands of leads with tiered pricing
- **Data Quality**: Confidence scoring for reliable information

### For Development Teams
- **Modern Architecture**: Clean, maintainable codebase
- **Production Ready**: Battle-tested with proper error handling
- **Extensible**: Easy to add new features and integrations
- **Documentation**: Comprehensive API documentation
- **Testing**: Robust test coverage for critical functionality

### For End Users
- **Intuitive Interface**: Clean, modern UI with real-time updates
- **Responsive Design**: Works on all devices
- **Fast Performance**: Optimized for speed and efficiency
- **Real-time Feedback**: Progress tracking for long-running tasks
- **Customization**: Flexible message styles and processing options

### Technical Benefits
- **High Performance**: Asynchronous processing with background tasks
- **Reliability**: Comprehensive error handling and fallback mechanisms
- **Security**: JWT authentication with secure token management
- **Scalability**: Designed to handle enterprise-level usage
- **Maintainability**: Clean architecture with separation of concerns

## Development Guidelines

### Code Standards
- **Backend**: Follow PEP 8 Python style guide
- **Frontend**: Use functional components with hooks
- **API**: Consistent RESTful design patterns
- **Database**: Proper ORM usage with SQLAlchemy

### Error Handling
- **Backend**: Comprehensive exception handling with structured logging
- **Frontend**: Graceful error states with user-friendly messages
- **Network**: Automatic retry mechanisms for transient failures

### Security
- **Authentication**: JWT tokens with proper expiration
- **Input Validation**: Pydantic schemas for request validation
- **SQL Injection**: ORM-based queries to prevent injection
- **Rate Limiting**: Built-in protection against abuse

### Performance
- **Caching**: Redis for frequently accessed data
- **Background Processing**: Celery for long-running tasks
- **Database Optimization**: Proper indexing and query optimization
- **Frontend Optimization**: Code splitting and lazy loading

---

## License

This project is open source and available under the MIT License.

## Support

For support, please open an issue in the repository or contact the development team.