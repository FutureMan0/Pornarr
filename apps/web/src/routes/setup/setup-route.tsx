/** A focused first-run flow that records only configuration the API can honour. */
import { Button, Input, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import type { FormEvent, JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router-dom";
import { ErrorScreen } from "../../errors/error-screen";
import { isRetryableError, messageForError, nextStepForError } from "../../lib/api-error";
import {
  type SetupPathValidation,
  useCompleteSetup,
  useLibraryPathValidation,
  useSetupStatus,
} from "./setup";

const STEPS = ["account", "library", "filters", "metadata", "summary"] as const;
type SetupStep = (typeof STEPS)[number];
type PasswordStrength = "weak" | "fair" | "strong";

const FILTER_RULES = [
  "term",
  "tag",
  "performer",
  "minimumConfidence",
  "unknownPerformerAge",
  "unknownFileType",
] as const;

function passwordStrength(password: string): PasswordStrength {
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter((pattern) =>
    pattern.test(password),
  ).length;
  if (password.length >= 14 && kinds >= 3) return "strong";
  if (password.length >= 12 && kinds >= 2) return "fair";
  return "weak";
}

export function SetupRoute(): JSX.Element {
  const { t } = useTranslation();
  const setup = useSetupStatus();
  const validateLibraryPath = useLibraryPathValidation();
  const completeSetup = useCompleteSetup();
  const [step, setStep] = useState<SetupStep>("account");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [libraryPath, setLibraryPath] = useState("");
  const [accountError, setAccountError] = useState<{ username?: string; password?: string }>({});
  const [libraryError, setLibraryError] = useState<string>();
  const [validatedPath, setValidatedPath] = useState<{
    readonly path: string;
    readonly result: SetupPathValidation;
  }>();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousStepRef = useRef<SetupStep | undefined>(undefined);
  const stepIndex = STEPS.indexOf(step);
  const strength = passwordStrength(password);

  // A field that has become valid must stop showing its error, or the form
  // tells the user their accepted input is wrong.
  const clearAccountError = (field: "username" | "password"): void => {
    setAccountError((current) => {
      if (current[field] === undefined) return current;
      const { [field]: _cleared, ...rest } = current;
      return rest;
    });
  };

  useEffect(() => {
    if (previousStepRef.current !== undefined && previousStepRef.current !== step) {
      headingRef.current?.focus();
    }
    previousStepRef.current = step;
  }, [step]);

  if (setup.isPending) {
    return (
      <main className="p-6">
        <SkeletonRegion label={t("setup.checking")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      </main>
    );
  }

  if (setup.isError) {
    return (
      <main className="p-6">
        <ErrorScreen
          title={messageForError(setup.error)}
          nextStep={nextStepForError(setup.error)}
          onRetry={
            isRetryableError(setup.error)
              ? () => {
                  void setup.refetch();
                }
              : undefined
          }
        />
      </main>
    );
  }

  if (completeSetup.isSuccess && completeSetup.data !== undefined) {
    return (
      <main className="mx-auto flex min-h-full w-full max-w-[calc(var(--space-16)*6)] flex-col justify-center gap-6 p-6">
        <section className="flex flex-col gap-3" aria-labelledby="setup-complete-title">
          <h1 id="setup-complete-title" className="text-xl text-ink" tabIndex={-1}>
            {t("setup.complete.title")}
          </h1>
          <p className="text-base text-ink-muted">{t("setup.complete.body")}</p>
          {completeSetup.data.same_filesystem_as_downloads ? null : (
            <p role="alert" className="rounded-sm bg-warning-weak p-3 text-sm text-ink">
              {t("setup.library.crossFilesystem")}
            </p>
          )}
          <Link className="text-sm text-primary underline" to="/login">
            {t("setup.complete.signIn")}
          </Link>
        </section>
      </main>
    );
  }

  if (setup.data.configured) return <Navigate to="/login" replace />;

  const checkLibraryPath = async (): Promise<SetupPathValidation | undefined> => {
    const path = libraryPath.trim();
    if (path === "") {
      setLibraryError(t("setup.library.pathRequired"));
      return undefined;
    }
    setLibraryError(undefined);
    try {
      const result = await validateLibraryPath.mutateAsync({ library_path: path });
      setValidatedPath({ path, result });
      return result;
    } catch (error) {
      setValidatedPath(undefined);
      setLibraryError(messageForError(error));
      return undefined;
    }
  };

  const next = async (): Promise<void> => {
    if (step === "account") {
      const nextError = {
        ...(username.trim() === "" ? { username: t("setup.account.usernameRequired") } : {}),
        ...(strength === "weak" ? { password: t("setup.account.passwordTooWeak") } : {}),
      };
      setAccountError(nextError);
      if (Object.keys(nextError).length === 0) setStep("library");
      return;
    }
    if (step === "library") {
      const path = libraryPath.trim();
      if (validatedPath?.path !== path) {
        const result = await checkLibraryPath();
        if (result?.same_filesystem_as_downloads) setStep("filters");
        return;
      }
      if (validatedPath.result.same_filesystem_as_downloads === false) {
        setStep("filters");
        return;
      }
    }
    if (stepIndex < STEPS.length - 1) setStep(STEPS[stepIndex + 1] as SetupStep);
  };

  const previous = (): void => {
    if (stepIndex > 0) setStep(STEPS[stepIndex - 1] as SetupStep);
  };

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (step === "summary") {
      completeSetup.mutate({
        username: username.trim(),
        password,
        library_path: libraryPath.trim(),
      });
      return;
    }
    void next();
  };

  return (
    <main className="mx-auto flex min-h-full w-full max-w-[calc(var(--space-16)*8)] flex-col justify-center gap-8 p-6">
      <header className="flex flex-col gap-3">
        <p className="text-sm text-ink-muted">
          {t("setup.progress", { current: stepIndex + 1, total: STEPS.length })}
        </p>
        <ol className="flex flex-wrap gap-2" aria-label={t("setup.progressLabel")}>
          {STEPS.map((item, index) => (
            <li
              key={item}
              aria-current={item === step ? "step" : undefined}
              className={
                item === step
                  ? "rounded-sm bg-primary-weak px-2 py-1 text-sm text-ink"
                  : "rounded-sm bg-surface-2 px-2 py-1 text-sm text-ink-muted"
              }
            >
              {t(`setup.steps.${item}`)}
              <span className="visually-hidden">
                {t("setup.stepNumber", { number: index + 1 })}
              </span>
            </li>
          ))}
        </ol>
      </header>

      <form className="flex flex-col gap-6" onSubmit={submit} noValidate>
        {step === "account" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.account.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.account.body")}</p>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-username">
                {t("setup.account.username")}
              </label>
              <Input
                id="setup-username"
                name="username"
                autoComplete="username"
                value={username}
                {...(accountError.username === undefined ? {} : { error: accountError.username })}
                onChange={(event) => {
                  setUsername(event.target.value);
                  if (event.target.value.trim() !== "") clearAccountError("username");
                }}
              />
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-password">
                {t("setup.account.password")}
              </label>
              <Input
                id="setup-password"
                name="password"
                type="password"
                autoComplete="new-password"
                value={password}
                {...(accountError.password === undefined ? {} : { error: accountError.password })}
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (passwordStrength(event.target.value) !== "weak") clearAccountError("password");
                }}
              />
              <p className="text-sm text-ink-muted">{t(`setup.account.strength.${strength}`)}</p>
            </div>
          </section>
        ) : null}

        {step === "library" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.library.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.library.body")}</p>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-library-path">
                {t("setup.library.path")}
              </label>
              <Input
                id="setup-library-path"
                name="library-path"
                value={libraryPath}
                {...(libraryError === undefined ? {} : { error: libraryError })}
                onChange={(event) => {
                  setLibraryPath(event.target.value);
                  setValidatedPath(undefined);
                }}
              />
            </div>
            {validatedPath?.result.same_filesystem_as_downloads === false ? (
              <p role="alert" className="rounded-sm bg-warning-weak p-3 text-sm text-ink">
                {t("setup.library.crossFilesystem")}
              </p>
            ) : null}
          </section>
        ) : null}

        {step === "filters" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.filters.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.filters.body")}</p>
            <ul className="flex flex-col gap-2" aria-label={t("setup.filters.listLabel")}>
              {FILTER_RULES.map((rule) => (
                <li key={rule} className="flex flex-col gap-1 rounded-sm bg-surface p-3">
                  <div className="flex items-center justify-between gap-3">
                    <h2 className="text-md text-ink">{t(`setup.filters.rules.${rule}.title`)}</h2>
                    <span className="text-sm text-ink-muted">{t("setup.filters.off")}</span>
                  </div>
                  <p className="text-sm text-ink-muted">{t(`setup.filters.rules.${rule}.body`)}</p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {step === "metadata" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.metadata.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.metadata.body")}</p>
            <p className="text-sm text-ink-muted">{t("setup.metadata.skipped")}</p>
          </section>
        ) : null}

        {step === "summary" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.summary.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.summary.body")}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-sm bg-surface p-4 text-sm">
              <dt className="text-ink-muted">{t("setup.summary.account")}</dt>
              <dd className="m-0 text-ink">{username}</dd>
              <dt className="text-ink-muted">{t("setup.summary.library")}</dt>
              <dd className="m-0 font-mono text-ink">{libraryPath}</dd>
              <dt className="text-ink-muted">{t("setup.summary.filters")}</dt>
              <dd className="m-0 text-ink">{t("setup.summary.filtersOff")}</dd>
              <dt className="text-ink-muted">{t("setup.summary.metadata")}</dt>
              <dd className="m-0 text-ink">{t("setup.summary.metadataSkipped")}</dd>
            </dl>
            {completeSetup.isError ? (
              <p role="alert" className="rounded-sm bg-danger-weak p-3 text-sm text-ink">
                {messageForError(completeSetup.error)}
              </p>
            ) : null}
          </section>
        ) : null}

        <footer className="flex flex-wrap justify-between gap-3">
          {stepIndex > 0 ? (
            <Button variant="secondary" onClick={previous}>
              {t("setup.back")}
            </Button>
          ) : (
            <span />
          )}
          {step === "summary" ? (
            <Button type="submit" loading={completeSetup.isPending}>
              {t("setup.complete.action")}
            </Button>
          ) : (
            <Button type="submit" loading={validateLibraryPath.isPending}>
              {step === "metadata"
                ? t("setup.metadata.skipAction")
                : step === "library" && validatedPath?.result.same_filesystem_as_downloads === false
                  ? t("setup.library.continueWithCopy")
                  : t("setup.next")}
            </Button>
          )}
        </footer>
      </form>
    </main>
  );
}
