/** A focused first-run flow that records only configuration the API can honour. */
import { Button, Checkbox, Input, Select, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import type { FormEvent, JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router-dom";
import { useLogin } from "../../auth/session";
import { ErrorScreen } from "../../errors/error-screen";
import { isRetryableError, messageForError, nextStepForError } from "../../lib/api-error";
import {
  type FilterAction,
  type FilterRuleKind,
  type FilterRuleWrite,
  type SetupDownloadClientImplementation,
  type SetupDownloadClientWrite,
  type SetupIndexerImplementation,
  type SetupIndexerWrite,
  type SetupMetadataProviderImplementation,
  type SetupPathValidation,
  useApplyFilterProfile,
  useCompleteSetup,
  useLibraryPathValidation,
  useSetupStatus,
  useTestDownloadClientConnection,
  useTestIndexerConnection,
} from "./setup";

const STEPS = [
  "account",
  "library",
  "indexer",
  "downloadClient",
  "filters",
  "metadata",
  "summary",
] as const;
type SetupStep = (typeof STEPS)[number];
type PasswordStrength = "weak" | "fair" | "strong";

/**
 * The six rules the database ships, in the order it ships them.
 *
 * `name` is the translation key and `kind` is what the API calls the rule.
 * They differ only in case convention, and spelling both out here is what keeps
 * a rename on either side from silently writing a rule nobody asked for.
 */
const FILTER_RULES = [
  { name: "term", kind: "term", patternLabel: "setup.filters.rules.term.pattern" },
  { name: "tag", kind: "tag", patternLabel: "setup.filters.rules.tag.pattern" },
  { name: "performer", kind: "performer", patternLabel: "setup.filters.rules.performer.pattern" },
  {
    name: "minimumConfidence",
    kind: "minimum_confidence",
    patternLabel: "setup.filters.rules.minimumConfidence.pattern",
  },
  { name: "unknownPerformerAge", kind: "unknown_performer_age" },
  { name: "unknownFileType", kind: "unknown_file_type" },
] as const satisfies readonly {
  name: string;
  kind: FilterRuleKind;
  patternLabel?: string;
}[];

/**
 * The kinds that match on something typed. The other three read a property of
 * the file, which is also why they are the ones without a pattern label.
 */
const PATTERN_KINDS: ReadonlySet<FilterRuleKind> = new Set<FilterRuleKind>(
  FILTER_RULES.filter((rule) => "patternLabel" in rule).map((rule) => rule.kind),
);

const FILTER_ACTIONS = ["reject", "quarantine", "allow"] as const;

interface FilterDraft {
  readonly enabled: boolean;
  readonly pattern: string;
  readonly action: FilterAction;
}

/** What migration 0004 inserts: every rule off, empty, and set to reject. */
function shippedFilters(): Record<FilterRuleKind, FilterDraft> {
  return Object.fromEntries(
    FILTER_RULES.map((rule) => [rule.kind, { enabled: false, pattern: "", action: "reject" }]),
  ) as Record<FilterRuleKind, FilterDraft>;
}

type IndexerFieldError = { baseUrl?: string; apiKey?: string };
type DownloadClientFieldError = {
  host?: string;
  port?: string;
  username?: string;
  password?: string;
  apiKey?: string;
};

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
  const testIndexerConnection = useTestIndexerConnection();
  const testDownloadClientConnection = useTestDownloadClientConnection();
  const completeSetup = useCompleteSetup();
  const login = useLogin();
  const applyFilterProfile = useApplyFilterProfile();
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

  const [indexerImplementation, setIndexerImplementation] =
    useState<SetupIndexerImplementation>("torznab");
  const [indexerBaseUrl, setIndexerBaseUrl] = useState("");
  const [indexerApiKey, setIndexerApiKey] = useState("");
  const [indexerError, setIndexerError] = useState<IndexerFieldError>({});
  const [indexerTestError, setIndexerTestError] = useState<string>();
  // Set once the exact fields above have passed a live connection test; any
  // edit to those fields clears it, so a stale pass can never be submitted.
  const [indexerConfig, setIndexerConfig] = useState<SetupIndexerWrite>();
  const indexerHasInput = indexerBaseUrl.trim() !== "" || indexerApiKey.trim() !== "";

  const [downloadClientImplementation, setDownloadClientImplementation] =
    useState<SetupDownloadClientImplementation>("qbittorrent");
  const [downloadClientHost, setDownloadClientHost] = useState("");
  const [downloadClientPort, setDownloadClientPort] = useState("");
  const [downloadClientUsername, setDownloadClientUsername] = useState("");
  const [downloadClientPassword, setDownloadClientPassword] = useState("");
  const [downloadClientApiKey, setDownloadClientApiKey] = useState("");
  const [downloadClientCategory, setDownloadClientCategory] = useState("");
  const [downloadClientError, setDownloadClientError] = useState<DownloadClientFieldError>({});
  const [downloadClientTestError, setDownloadClientTestError] = useState<string>();
  const [downloadClientConfig, setDownloadClientConfig] = useState<SetupDownloadClientWrite>();
  const downloadClientHasInput =
    downloadClientHost.trim() !== "" ||
    downloadClientPort.trim() !== "" ||
    (downloadClientImplementation === "qbittorrent"
      ? downloadClientUsername.trim() !== "" || downloadClientPassword !== ""
      : downloadClientApiKey.trim() !== "");

  const [filters, setFilters] = useState<Record<FilterRuleKind, FilterDraft>>(shippedFilters);
  const [filterError, setFilterError] = useState<Partial<Record<FilterRuleKind, string>>>({});
  const enabledFilters = FILTER_RULES.filter((rule) => filters[rule.kind].enabled);

  const [metadataImplementation, setMetadataImplementation] =
    useState<SetupMetadataProviderImplementation>("stashdb");
  const [metadataApiKey, setMetadataApiKey] = useState("");
  const [metadataEndpoint, setMetadataEndpoint] = useState("");

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

  const editFilter = (kind: FilterRuleKind, change: Partial<FilterDraft>): void => {
    setFilters((current) => ({ ...current, [kind]: { ...current[kind], ...change } }));
    setFilterError((current) => {
      if (current[kind] === undefined) return current;
      const { [kind]: _cleared, ...rest } = current;
      return rest;
    });
  };

  const clearIndexerError = (field: keyof IndexerFieldError): void => {
    setIndexerError((current) => {
      if (current[field] === undefined) return current;
      const { [field]: _cleared, ...rest } = current;
      return rest;
    });
  };

  const clearDownloadClientError = (field: keyof DownloadClientFieldError): void => {
    setDownloadClientError((current) => {
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
          {/* The instance is up either way, so this is a warning rather than a
              failure — but it must be said, because the rules the operator
              turned on are the ones that are not running. */}
          {login.isError || applyFilterProfile.isError ? (
            <p role="alert" className="rounded-sm bg-warning-weak p-3 text-sm text-ink">
              {t("setup.filters.applyFailed")}{" "}
              {messageForError(applyFilterProfile.error ?? login.error)}
            </p>
          ) : null}
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
        if (result?.same_filesystem_as_downloads) setStep("indexer");
        return;
      }
      if (validatedPath.result.same_filesystem_as_downloads === false) {
        setStep("indexer");
        return;
      }
    }
    if (step === "indexer") {
      if (!indexerHasInput) {
        setIndexerError({});
        setIndexerTestError(undefined);
        setStep("downloadClient");
        return;
      }
      if (indexerConfig !== undefined) {
        setStep("downloadClient");
        return;
      }
      const nextError: IndexerFieldError = {
        ...(indexerBaseUrl.trim() === "" ? { baseUrl: t("setup.indexer.baseUrlRequired") } : {}),
        ...(indexerApiKey.trim() === "" ? { apiKey: t("setup.indexer.apiKeyRequired") } : {}),
      };
      setIndexerError(nextError);
      if (Object.keys(nextError).length > 0) return;
      const candidate: SetupIndexerWrite = {
        implementation: indexerImplementation,
        base_url: indexerBaseUrl.trim(),
        api_key: indexerApiKey.trim(),
      };
      setIndexerTestError(undefined);
      try {
        await testIndexerConnection.mutateAsync(candidate);
        setIndexerConfig(candidate);
        setStep("downloadClient");
      } catch (error) {
        setIndexerTestError(messageForError(error));
      }
      return;
    }
    if (step === "downloadClient") {
      if (!downloadClientHasInput) {
        setDownloadClientError({});
        setDownloadClientTestError(undefined);
        setStep("filters");
        return;
      }
      if (downloadClientConfig !== undefined) {
        setStep("filters");
        return;
      }
      const port = Number.parseInt(downloadClientPort, 10);
      const nextError: DownloadClientFieldError = {
        ...(downloadClientHost.trim() === ""
          ? { host: t("setup.downloadClient.hostRequired") }
          : {}),
        ...(downloadClientPort.trim() === "" || Number.isNaN(port) || port < 1 || port > 65535
          ? { port: t("setup.downloadClient.portRequired") }
          : {}),
        ...(downloadClientImplementation === "qbittorrent"
          ? {
              ...(downloadClientUsername.trim() === ""
                ? { username: t("setup.downloadClient.usernameRequired") }
                : {}),
              ...(downloadClientPassword === ""
                ? { password: t("setup.downloadClient.passwordRequired") }
                : {}),
            }
          : downloadClientApiKey.trim() === ""
            ? { apiKey: t("setup.downloadClient.apiKeyRequired") }
            : {}),
      };
      setDownloadClientError(nextError);
      if (Object.keys(nextError).length > 0) return;
      const candidate: SetupDownloadClientWrite = {
        implementation: downloadClientImplementation,
        host: downloadClientHost.trim(),
        port,
        credentials:
          downloadClientImplementation === "qbittorrent"
            ? JSON.stringify({
                username: downloadClientUsername.trim(),
                password: downloadClientPassword,
              })
            : downloadClientApiKey.trim(),
        category: downloadClientCategory.trim() === "" ? null : downloadClientCategory.trim(),
      };
      setDownloadClientTestError(undefined);
      try {
        await testDownloadClientConnection.mutateAsync(candidate);
        setDownloadClientConfig(candidate);
        setStep("filters");
      } catch (error) {
        setDownloadClientTestError(messageForError(error));
      }
      return;
    }
    if (step === "filters") {
      // The same rule the API enforces, said before the request rather than
      // after it: an enabled rule that matches on nothing matches everything or
      // nothing, and either way it is not what the operator meant.
      const nextError: Partial<Record<FilterRuleKind, string>> = {};
      for (const rule of enabledFilters) {
        if (!PATTERN_KINDS.has(rule.kind)) continue;
        const pattern = filters[rule.kind].pattern.trim();
        if (rule.kind === "minimum_confidence") {
          const threshold = Number(pattern);
          if (pattern === "" || Number.isNaN(threshold) || threshold < 0 || threshold > 1) {
            nextError[rule.kind] = t("setup.filters.confidenceInvalid");
          }
          continue;
        }
        if (pattern === "") nextError[rule.kind] = t("setup.filters.patternRequired");
      }
      setFilterError(nextError);
      if (Object.keys(nextError).length > 0) return;
    }
    if (stepIndex < STEPS.length - 1) setStep(STEPS[stepIndex + 1] as SetupStep);
  };

  const filterWrites = (): FilterRuleWrite[] =>
    FILTER_RULES.map((rule) => ({
      kind: rule.kind,
      // A rule that reads a property of the file takes no pattern, and the API
      // refuses one rather than storing a value it would never read.
      pattern: PATTERN_KINDS.has(rule.kind) ? filters[rule.kind].pattern.trim() : "",
      action: filters[rule.kind].action,
      enabled: filters[rule.kind].enabled,
    }));

  /**
   * Create the instance, then write the filter rules onto it.
   *
   * Two requests because `/api/setup/complete` does not carry them and the
   * profile they belong to is administrator-owned (ADR 0018). Signing in
   * between the two is the same request the "Sign in" link on the next screen
   * makes, and it only happens when the operator turned something on: an
   * instance with every rule off is already exactly what the migration seeded.
   */
  const completeAndApplyFilters = async (): Promise<void> => {
    try {
      await completeSetup.mutateAsync({
        username: username.trim(),
        password,
        library_path: libraryPath.trim(),
        ...(indexerConfig === undefined ? {} : { indexer: indexerConfig }),
        ...(downloadClientConfig === undefined ? {} : { download_client: downloadClientConfig }),
        ...(metadataApiKey.trim() === ""
          ? {}
          : {
              metadata_provider: {
                implementation: metadataImplementation,
                api_key: metadataApiKey.trim(),
                endpoint: metadataEndpoint.trim() === "" ? null : metadataEndpoint.trim(),
              },
            }),
      });
    } catch {
      // Nothing was created; the summary renders `completeSetup.error`.
      return;
    }
    if (enabledFilters.length === 0) return;
    try {
      await login.mutateAsync({ username: username.trim(), password });
      await applyFilterProfile.mutateAsync(filterWrites());
    } catch {
      // The instance exists and the rules did not land. The completion screen
      // says both rather than reporting a success it cannot vouch for.
    }
  };

  const previous = (): void => {
    if (stepIndex > 0) setStep(STEPS[stepIndex - 1] as SetupStep);
  };

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (step === "summary") {
      void completeAndApplyFilters();
      return;
    }
    void next();
  };

  const nextLabel = ((): string => {
    if (step === "indexer")
      return indexerHasInput ? t("setup.next") : t("setup.indexer.skipAction");
    if (step === "downloadClient") {
      return downloadClientHasInput ? t("setup.next") : t("setup.downloadClient.skipAction");
    }
    if (step === "metadata") {
      return metadataApiKey.trim() === "" ? t("setup.metadata.skipAction") : t("setup.next");
    }
    if (step === "library" && validatedPath?.result.same_filesystem_as_downloads === false) {
      return t("setup.library.continueWithCopy");
    }
    return t("setup.next");
  })();
  const nextLoading =
    validateLibraryPath.isPending ||
    testIndexerConnection.isPending ||
    testDownloadClientConnection.isPending;

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
                // The rule was only ever stated by rejecting a password that
                // broke it, which is the wrong moment to learn it.
                aria-describedby="setup-password-rule"
                value={password}
                {...(accountError.password === undefined ? {} : { error: accountError.password })}
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (passwordStrength(event.target.value) !== "weak")
                    clearAccountError("password");
                }}
              />
              <p id="setup-password-rule" className="text-sm text-ink-muted">
                {t("setup.account.passwordRule")}
              </p>
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

        {step === "indexer" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.indexer.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.indexer.body")}</p>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-indexer-implementation">
                {t("setup.indexer.implementation")}
              </label>
              <Select
                id="setup-indexer-implementation"
                value={indexerImplementation}
                onChange={(event) => {
                  setIndexerImplementation(event.target.value as SetupIndexerImplementation);
                  setIndexerConfig(undefined);
                }}
              >
                <option value="torznab">{t("setup.indexer.implementations.torznab")}</option>
                <option value="newznab">{t("setup.indexer.implementations.newznab")}</option>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-indexer-base-url">
                {t("setup.indexer.baseUrl")}
              </label>
              <Input
                id="setup-indexer-base-url"
                name="indexer-base-url"
                value={indexerBaseUrl}
                {...(indexerError.baseUrl === undefined ? {} : { error: indexerError.baseUrl })}
                onChange={(event) => {
                  setIndexerBaseUrl(event.target.value);
                  setIndexerConfig(undefined);
                  if (event.target.value.trim() !== "") clearIndexerError("baseUrl");
                }}
              />
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-indexer-api-key">
                {t("setup.indexer.apiKey")}
              </label>
              <Input
                id="setup-indexer-api-key"
                name="indexer-api-key"
                type="password"
                autoComplete="off"
                value={indexerApiKey}
                {...(indexerError.apiKey === undefined ? {} : { error: indexerError.apiKey })}
                onChange={(event) => {
                  setIndexerApiKey(event.target.value);
                  setIndexerConfig(undefined);
                  if (event.target.value.trim() !== "") clearIndexerError("apiKey");
                }}
              />
            </div>
            {indexerTestError === undefined ? null : (
              <p role="alert" className="rounded-sm bg-danger-weak p-3 text-sm text-ink">
                {indexerTestError}
              </p>
            )}
          </section>
        ) : null}

        {step === "downloadClient" ? (
          <section className="flex flex-col gap-4" aria-labelledby="setup-title">
            <h1 ref={headingRef} id="setup-title" className="text-xl text-ink" tabIndex={-1}>
              {t("setup.downloadClient.title")}
            </h1>
            <p className="text-base text-ink-muted">{t("setup.downloadClient.body")}</p>
            <div className="flex flex-col gap-2">
              <label
                className="text-sm text-ink-muted"
                htmlFor="setup-download-client-implementation"
              >
                {t("setup.downloadClient.implementation")}
              </label>
              <Select
                id="setup-download-client-implementation"
                value={downloadClientImplementation}
                onChange={(event) => {
                  setDownloadClientImplementation(
                    event.target.value as SetupDownloadClientImplementation,
                  );
                  setDownloadClientConfig(undefined);
                }}
              >
                <option value="qbittorrent">
                  {t("setup.downloadClient.implementations.qbittorrent")}
                </option>
                <option value="sabnzbd">{t("setup.downloadClient.implementations.sabnzbd")}</option>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-download-client-host">
                {t("setup.downloadClient.host")}
              </label>
              <Input
                id="setup-download-client-host"
                name="download-client-host"
                value={downloadClientHost}
                {...(downloadClientError.host === undefined
                  ? {}
                  : { error: downloadClientError.host })}
                onChange={(event) => {
                  setDownloadClientHost(event.target.value);
                  setDownloadClientConfig(undefined);
                  if (event.target.value.trim() !== "") clearDownloadClientError("host");
                }}
              />
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-download-client-port">
                {t("setup.downloadClient.port")}
              </label>
              <Input
                id="setup-download-client-port"
                name="download-client-port"
                type="number"
                min={1}
                max={65535}
                value={downloadClientPort}
                {...(downloadClientError.port === undefined
                  ? {}
                  : { error: downloadClientError.port })}
                onChange={(event) => {
                  setDownloadClientPort(event.target.value);
                  setDownloadClientConfig(undefined);
                  if (event.target.value.trim() !== "") clearDownloadClientError("port");
                }}
              />
            </div>
            {downloadClientImplementation === "qbittorrent" ? (
              <>
                <div className="flex flex-col gap-2">
                  <label
                    className="text-sm text-ink-muted"
                    htmlFor="setup-download-client-username"
                  >
                    {t("setup.downloadClient.username")}
                  </label>
                  <Input
                    id="setup-download-client-username"
                    name="download-client-username"
                    autoComplete="off"
                    value={downloadClientUsername}
                    {...(downloadClientError.username === undefined
                      ? {}
                      : { error: downloadClientError.username })}
                    onChange={(event) => {
                      setDownloadClientUsername(event.target.value);
                      setDownloadClientConfig(undefined);
                      if (event.target.value.trim() !== "") clearDownloadClientError("username");
                    }}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <label
                    className="text-sm text-ink-muted"
                    htmlFor="setup-download-client-password"
                  >
                    {t("setup.downloadClient.password")}
                  </label>
                  <Input
                    id="setup-download-client-password"
                    name="download-client-password"
                    type="password"
                    autoComplete="off"
                    value={downloadClientPassword}
                    {...(downloadClientError.password === undefined
                      ? {}
                      : { error: downloadClientError.password })}
                    onChange={(event) => {
                      setDownloadClientPassword(event.target.value);
                      setDownloadClientConfig(undefined);
                      if (event.target.value !== "") clearDownloadClientError("password");
                    }}
                  />
                </div>
              </>
            ) : (
              <div className="flex flex-col gap-2">
                <label className="text-sm text-ink-muted" htmlFor="setup-download-client-api-key">
                  {t("setup.downloadClient.apiKey")}
                </label>
                <Input
                  id="setup-download-client-api-key"
                  name="download-client-api-key"
                  type="password"
                  autoComplete="off"
                  value={downloadClientApiKey}
                  {...(downloadClientError.apiKey === undefined
                    ? {}
                    : { error: downloadClientError.apiKey })}
                  onChange={(event) => {
                    setDownloadClientApiKey(event.target.value);
                    setDownloadClientConfig(undefined);
                    if (event.target.value.trim() !== "") clearDownloadClientError("apiKey");
                  }}
                />
              </div>
            )}
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-download-client-category">
                {t("setup.downloadClient.category")}
              </label>
              <Input
                id="setup-download-client-category"
                name="download-client-category"
                value={downloadClientCategory}
                onChange={(event) => {
                  setDownloadClientCategory(event.target.value);
                  setDownloadClientConfig(undefined);
                }}
              />
            </div>
            {downloadClientTestError === undefined ? null : (
              <p role="alert" className="rounded-sm bg-danger-weak p-3 text-sm text-ink">
                {downloadClientTestError}
              </p>
            )}
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
                <li key={rule.kind} className="flex flex-col gap-2 rounded-sm bg-surface p-3">
                  <Checkbox
                    label={t(`setup.filters.rules.${rule.name}.title`)}
                    checked={filters[rule.kind].enabled}
                    aria-describedby={`setup-filter-${rule.kind}-body`}
                    onChange={(event) => editFilter(rule.kind, { enabled: event.target.checked })}
                  />
                  <p id={`setup-filter-${rule.kind}-body`} className="text-sm text-ink-muted">
                    {t(`setup.filters.rules.${rule.name}.body`)}
                  </p>
                  {/* The controls appear only once the rule is on. A pattern box
                      beside a rule nobody turned on is a field that does
                      nothing, six times over. */}
                  {filters[rule.kind].enabled ? (
                    <div className="flex flex-col gap-2">
                      {"patternLabel" in rule ? (
                        <div className="flex flex-col gap-2">
                          <label
                            className="text-sm text-ink-muted"
                            htmlFor={`setup-filter-${rule.kind}-pattern`}
                          >
                            {t(rule.patternLabel)}
                          </label>
                          <Input
                            id={`setup-filter-${rule.kind}-pattern`}
                            name={`filter-${rule.kind}-pattern`}
                            inputMode={rule.kind === "minimum_confidence" ? "decimal" : undefined}
                            value={filters[rule.kind].pattern}
                            {...(filterError[rule.kind] === undefined
                              ? {}
                              : { error: filterError[rule.kind] })}
                            onChange={(event) =>
                              editFilter(rule.kind, { pattern: event.target.value })
                            }
                          />
                        </div>
                      ) : null}
                      <div className="flex flex-col gap-2">
                        <label
                          className="text-sm text-ink-muted"
                          htmlFor={`setup-filter-${rule.kind}-action`}
                        >
                          {t("setup.filters.action")}
                        </label>
                        <Select
                          id={`setup-filter-${rule.kind}-action`}
                          name={`filter-${rule.kind}-action`}
                          value={filters[rule.kind].action}
                          onChange={(event) =>
                            editFilter(rule.kind, { action: event.target.value as FilterAction })
                          }
                        >
                          {FILTER_ACTIONS.map((action) => (
                            <option key={action} value={action}>
                              {t(`setup.filters.actions.${action}`)}
                            </option>
                          ))}
                        </Select>
                      </div>
                    </div>
                  ) : null}
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
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-metadata-implementation">
                {t("setup.metadata.implementation")}
              </label>
              <Select
                id="setup-metadata-implementation"
                value={metadataImplementation}
                onChange={(event) =>
                  setMetadataImplementation(
                    event.target.value as SetupMetadataProviderImplementation,
                  )
                }
              >
                <option value="stashdb">{t("metadataProviders.implementations.stashdb")}</option>
                <option value="tpdb">{t("metadataProviders.implementations.tpdb")}</option>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-metadata-api-key">
                {t("setup.metadata.apiKey")}
              </label>
              <Input
                id="setup-metadata-api-key"
                name="metadata-api-key"
                type="password"
                autoComplete="off"
                value={metadataApiKey}
                onChange={(event) => setMetadataApiKey(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-sm text-ink-muted" htmlFor="setup-metadata-endpoint">
                {t("setup.metadata.endpoint")}
              </label>
              <Input
                id="setup-metadata-endpoint"
                name="metadata-endpoint"
                value={metadataEndpoint}
                onChange={(event) => setMetadataEndpoint(event.target.value)}
              />
            </div>
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
              <dt className="text-ink-muted">{t("setup.summary.indexer")}</dt>
              <dd className="m-0 text-ink">
                {indexerConfig === undefined
                  ? t("setup.summary.indexerSkipped")
                  : t(`setup.indexer.implementations.${indexerConfig.implementation}`)}
              </dd>
              <dt className="text-ink-muted">{t("setup.summary.downloadClient")}</dt>
              <dd className="m-0 text-ink">
                {downloadClientConfig === undefined
                  ? t("setup.summary.downloadClientSkipped")
                  : t(
                      `setup.downloadClient.implementations.${downloadClientConfig.implementation}`,
                    )}
              </dd>
              <dt className="text-ink-muted">{t("setup.summary.filters")}</dt>
              <dd className="m-0 text-ink">
                {enabledFilters.length === 0
                  ? t("setup.summary.filtersOff")
                  : enabledFilters
                      .map((rule) => t(`setup.filters.rules.${rule.name}.title`))
                      .join(", ")}
              </dd>
              <dt className="text-ink-muted">{t("setup.summary.metadata")}</dt>
              <dd className="m-0 text-ink">
                {metadataApiKey.trim() === ""
                  ? t("setup.summary.metadataSkipped")
                  : t(`metadataProviders.implementations.${metadataImplementation}`)}
              </dd>
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
            <Button
              type="submit"
              loading={completeSetup.isPending || login.isPending || applyFilterProfile.isPending}
            >
              {t("setup.complete.action")}
            </Button>
          ) : (
            <Button type="submit" loading={nextLoading}>
              {nextLabel}
            </Button>
          )}
        </footer>
      </form>
    </main>
  );
}
