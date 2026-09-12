# PlayGraph accounts and connected platforms

Status: Phase 2 in progress. **App account sign-in is not enabled yet.** The owner wants
password, passkey and email-link choices, plus MFA. A Clerk development application
has been created and linked through the authenticated CLI. Development keys are
stored separately in ignored `.env.clerk`; the current app does not load them yet.
The CLI initializer did not recognize FastAPI/plain JavaScript, so integration
must follow the official JavaScript and backend verification guidance.
Existing Steam sign-in remains the functioning local beta path.

Phase 2A adds explicit Alembic migration/adoption with backup and preservation
tests. MIGRATIONS.md documents the required upgrade and recovery process. No
AuthIdentity rows, native account sessions or MFA enforcement exist yet.

## Provider decision

Use Clerk as the first integration candidate. Its documented options include
passwords, email verification links, passkeys, authenticator-app MFA and backup
codes. Passkeys are enrolled after initial sign-up and are scoped to a domain.
Development testing can use the available authentication features without a
production subscription; production passkeys and some other features require
a paid plan. Review pricing before committing to launch costs.
Sources: [Clerk sign-in options](https://clerk.com/docs/guides/configure/auth-strategies/sign-up-sign-in-options),
[pricing](https://clerk.com/pricing).

Auth0 is not an interchangeable choice for the exact requested hosted flow:
its documented email magic links require Classic Login, while passkeys use New
Universal Login. Do not silently replace an email link with an emailed code.
Sources: [Auth0 email links](https://auth0.com/docs/authenticate/passwordless/authentication-methods/email-magic-link),
[Auth0 passkeys](https://developer.auth0.com/resources/labs/authentication/implement-passwordless-login-with-passkeys).

## Setup dependency

1. Create a PlayGraph development application in the Clerk dashboard.
2. Enable verified email sign-up, passwords and same-browser email links.
   Enable passkeys and authenticator-app MFA with recovery codes. Clerk's current
   policy lets a verified passkey satisfy MFA; this is mandatory for newly
   created instances. Validate that policy through the provider's active session.
3. Choose one development domain, normally localhost, for both enrollment and
   sign-in. A passkey enrolled on a provider-hosted domain is not automatically
   usable on localhost. Keep development and production credentials separate.
4. Store provider settings through local environment configuration or a secret
   manager. A publishable browser key is different from the secret backend key.
   Never paste secret keys, recovery codes or session tokens into chat or Git.
5. Implement and test provider session verification and the account mapping
   below before offering sign-up in the app. Provider configuration alone does
   not enable these features in the current build.

No placeholder keys or fake email delivery are used. The pending integration
should use provider-maintained components and backend verification libraries,
with exact allowed issuer, audience/authorized party, algorithm and application
origin checks. Backend identity must come from a verified session, never a
browser-supplied user ID, email or display name.

## Data and session boundaries

Keep the existing internal User.id as the owner of reviews and personal data.
Add AuthIdentity(user_id, provider, issuer, subject) with a unique issuer/subject
constraint. Credentials and factors belong to the identity provider, not to
Steam or a PlayGraph password table. Add a per-user security/session version for
account-wide revocation. Verify provider revocation/factor-policy changes; do
not leave a local session authorized after its source identity loses access.

LinkedAccount remains separate and represents permission to import a platform's
data. A Steam link is not a second authentication method or recovery credential
for a migrated PlayGraph account. No automatic merges by matching email or names.

Move schema evolution to Alembic before adding these persistent identities.
Test an empty install and an upgrade containing the owner's existing users,
links, snapshots and reviews. Back up coherently and stop writers for upgrades.
Preserve every existing ID. create_all is not a migration system.

## Existing Steam account conversion

An existing user signs in through Steam and explicitly adds PlayGraph sign-in
after fresh proof. Bind the operation to the current user, session, browser,
intent and short-lived server state. Complete the provider's authentication
policy before attaching the verified issuer/subject to that same User.id.
Reject an identity already belonging to another user; never silently switch
owners, move reviews, or merge accounts.

After conversion, Steam-only callbacks cannot mint full PlayGraph sessions.
They must route through the account's normal provider/MFA policy. New platform
connections start from authenticated POST requests with CSRF, exact Origin and
recent reauthentication. Their callbacks recheck the initiating session and
target user before linking. Concurrent link attempts rely on database uniqueness.

Do not offer unlink/replacement until snapshots store their linked-account
source and retention rules are defined. The current user/game-only snapshot
key would otherwise leave the old account's library and verification data behind.

## Security acceptance gate

- Test wrong issuer, audience, signature, origin and expired/revoked sessions.
- Test forged subjects, cross-account linking, replayed callbacks and concurrent links.
- Prove that Steam login and API bearer paths cannot bypass enrolled MFA.
- Test recovery, factor changes, deletion and revocation across all tabs/sessions.
- Require recent reauthentication for link changes, factor changes and exports.
- Keep credential/factor material out of logs, analytics, URLs and browser storage
  owned by PlayGraph. Review the provider SDK's own storage and CSP requirements.
- Enforce provider MFA policy before issuing any fully authorized local session.
- Verify email delivery and domain setup. No reset tokens in UI or logs as a substitute.
- Keep users in a local/invited test environment until independent review and
  the privacy, backup and operational gates pass.

Provider use reduces the credential-management code we own; it cannot guarantee
that a site will never be compromised. Account linking, permissions, session
revocation, backups, staff access and incident response remain our responsibility.
See [OWASP MFA](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html)
and [session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
