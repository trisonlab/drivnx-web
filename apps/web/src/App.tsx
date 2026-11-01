export function App({ signedIn }: { signedIn: boolean }) {
  return (
    <>
      <h1>Conflux</h1>
      {!signedIn && (
        <p role="alert">
          This browser isn't signed in. Start Conflux again and use the new link it opens or prints.
        </p>
      )}
    </>
  );
}
