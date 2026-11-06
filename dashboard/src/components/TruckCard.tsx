import { formatDuration, formatScore, formatTime } from "../format";
import { href } from "../router";
import type { Truck } from "../api/types";

interface Props {
  truck: Truck;
  mediaUrl: (path: string) => string;
  /** Leaves out the duration, for the narrow latest-trucks strip. */
  compact?: boolean;
  /** Rings the card once; the animation is in CSS. */
  isNew?: boolean;
}

export default function TruckCard({ truck, mediaUrl, compact = false, isNew = false }: Props) {
  return (
    <a
      className={`card card--link${isNew ? " is-new" : ""}`}
      href={href({ page: "truck", id: truck.id })}
    >
      <div className="card-media">
        {/* Decorative here: the card title already names the truck. */}
        {truck.crop_url && <img src={mediaUrl(truck.crop_url)} alt="" loading="lazy" />}
      </div>
      <div className="card-title">Truck #{truck.id}</div>
      <div className="card-meta">
        <span>{formatTime(truck.first_seen)}</span>
        {!compact && <span>{formatDuration(truck.first_seen, truck.last_seen)}</span>}
        <span>{formatScore(truck.best_score)}</span>
        {truck.clip_url && <span className="badge badge--truck">clip</span>}
      </div>
    </a>
  );
}
