import { Component, type ReactNode } from "react";
import Banner from "./Banner";

interface Props {
  children: ReactNode;
  message: string;
  /** Tests inject a stand-in; the default reloads the page. */
  reload?: () => void;
}

/** Catches a page that fails to load (e.g. its code chunk is gone) so the rest of the app stays up. */
export default class LoadErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Banner tone="error">
        <span>{this.props.message}</span>{" "}
        <button
          type="button"
          className="button"
          onClick={() => (this.props.reload ?? (() => window.location.reload()))()}
        >
          Reload
        </button>
      </Banner>
    );
  }
}
