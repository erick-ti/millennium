import { Curve } from "@/components/landing/curve";
import { EngineRoom } from "@/components/landing/engine-room";
import { Hero } from "@/components/landing/hero";
import { Watch } from "@/components/landing/watch";

export default function HomePage() {
  return (
    <div className="relative bg-vault-950">
      <div className="vault-grain" />

      <Hero />
      <Curve />
      <Watch />
      <EngineRoom />
    </div>
  );
}
