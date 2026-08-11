/**
 * The last thing between a thrown render and a white page.
 *
 * React 19 still has no hook for this: `componentDidCatch` and
 * `getDerivedStateFromError` are class-only lifecycle methods and there is no
 * function-component equivalent, so the class below is the supported form rather
 * than a leftover. Everything that needs hooks — translation, the error screen —
 * lives in the function component it renders.
 *
 * Recovery is a remount, not a reload. Keying the subtree on an attempt counter
 * throws away the state that produced the crash and builds it again from
 * scratch, which is what "try again" has to mean if it is to be worth a button;
 * a full page load would also throw away the query cache, the route and the
 * scroll position, and is what the user can still do by hand if this fails
 * twice.
 */
import type { ErrorInfo, JSX, ReactNode } from "react";
import { Component, Fragment } from "react";
import { useTranslation } from "react-i18next";
import { ErrorScreen } from "./error-screen";

export interface ErrorBoundaryProps {
  readonly children: ReactNode;
  /** Passed through to the fallback, so a boundary can sit in a narrow column. */
  readonly className?: string | undefined;
}

interface ErrorBoundaryState {
  readonly failed: boolean;
  /** Bumped on every recovery, and used as the subtree's key. */
  readonly attempt: number;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { failed: false, attempt: 0 };

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { failed: true };
  }

  /**
   * The component stack exists nowhere else. It is not shown to the user — a
   * stack is not a next step — but without it a crash report is "a screen
   * failed", which nobody can act on.
   */
  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[pornarr] render failed", error, info.componentStack);
  }

  private readonly retry = (): void => {
    this.setState((previous) => ({ failed: false, attempt: previous.attempt + 1 }));
  };

  override render(): ReactNode {
    if (this.state.failed) {
      return <BoundaryFallback onRetry={this.retry} className={this.props.className} />;
    }
    return <Fragment key={this.state.attempt}>{this.props.children}</Fragment>;
  }
}

function BoundaryFallback({
  onRetry,
  className,
}: {
  readonly onRetry: () => void;
  readonly className?: string | undefined;
}): JSX.Element {
  const { t } = useTranslation();

  return (
    <ErrorScreen
      title={t("errors.brokenTitle")}
      nextStep={t("errors.brokenStep")}
      onRetry={onRetry}
      className={className}
    />
  );
}
