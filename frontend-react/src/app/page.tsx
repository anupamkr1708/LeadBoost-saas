import { AmbientBackground } from "@/components/shared/ambient-background";
import { Navbar } from "@/components/landing/navbar";
import { Hero } from "@/components/landing/hero";
import { Features } from "@/components/landing/features";
import { DiscoveryDemo } from "@/components/landing/discovery-demo";
import { PipelineVisualization } from "@/components/landing/pipeline-visualization";
import { Pricing } from "@/components/landing/pricing";
import { Faq } from "@/components/landing/faq";
import { Cta } from "@/components/landing/cta";
import { Footer } from "@/components/landing/footer";

export default function LandingPage() {
  return (
    <div className="relative">
      <AmbientBackground />
      <Navbar />
      <Hero />
      <Features />
      <DiscoveryDemo />
      <PipelineVisualization />
      <Pricing />
      <Faq />
      <Cta />
      <Footer />
    </div>
  );
}
