"""Refresh the Android tools' Bouncy Castle closure with verified official JARs."""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.request import urlopen


def fetch(url):
    with urlopen(url, timeout=90) as response:
        return response.read()


def resolve():
    result = {}
    for name in ("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on"):
        base = "https://repo.maven.apache.org/maven2/org/bouncycastle/" + name
        version = ET.fromstring(fetch(base + "/maven-metadata.xml")).findtext(
            "versioning/release"
        )
        if not version or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version):
            raise ValueError("invalid stable Bouncy Castle release")
        url = f"{base}/{version}/{name}-{version}.jar"
        checksum = fetch(url + ".sha256").decode().strip()
        if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise ValueError("invalid official JAR checksum")
        result[name] = {"version": version, "url": url, "sha256": checksum.lower()}
    if len({v["version"] for v in result.values()}) != 1:
        raise ValueError("Bouncy Castle closure releases do not agree")
    return result


def files(root):
    result = {}
    for name in ("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on"):
        matches = list(
            root.glob(f"lib/external/org/bouncycastle/{name}/*/{name}-*.jar")
        )
        if len(matches) != 1:
            raise ValueError("missing or ambiguous Android Bouncy Castle package")
        path = matches[0]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Android dependency escapes its tool root")
        result[name] = path
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classpaths(root, paths):
    result = {}
    for path in root.glob("lib/*classpath*.jar"):
        with zipfile.ZipFile(path) as archive:
            text = archive.read("META-INF/MANIFEST.MF").decode()
            text = text.replace("\r\n", "\n").replace("\n ", "")
            if "org/bouncycastle/" not in text:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Android classpath escapes its tool root")
            if not set(archive.namelist()) <= {
                "META-INF/",
                "META-INF/MANIFEST.MF",
                "resources/stamp.txt",
            }:
                raise ValueError("unsupported non-metadata Android classpath archive")
            if not all(
                str(p.relative_to(root / "lib")) in text for p in paths.values()
            ):
                raise ValueError("Android classpath has missing or stale BC references")
            result[path] = text
    if not result:
        raise ValueError("Android BC dependencies have no verified classpath consumer")
    return result


def rewrite_classpath(original, text):
    if not text.isascii():
        raise ValueError("unsupported non-ASCII Android classpath manifest")
    lines = []
    for line in text.splitlines():
        if not line:
            continue
        lines.append(line[:70])
        line = line[70:]
        while line:
            lines.append(" " + line[:69])
            line = line[69:]
    content = ("\r\n".join(lines) + "\r\n\r\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        with zipfile.ZipFile(buffer, "w") as output:
            for info in source.infolist():
                output.writestr(
                    info,
                    content
                    if info.filename == "META-INF/MANIFEST.MF"
                    else source.read(info),
                )
    return buffer.getvalue()


def probe(root, paths, version):
    with tempfile.TemporaryDirectory(prefix="runner-image-bc-probe-") as directory:
        work = Path(directory)
        source = work / "BouncyCastleProbe.java"
        source.write_text(
            """
        import java.security.*;
        import java.math.BigInteger;
        import java.util.*;
        import java.nio.charset.StandardCharsets;
        import java.io.*;
        import java.security.cert.X509Certificate;
        import org.bouncycastle.jce.provider.BouncyCastleProvider;
        import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder;
        import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter;
        import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder;
        import org.bouncycastle.cms.CMSProcessableByteArray;
        import org.bouncycastle.cms.CMSSignedDataGenerator;
        import org.bouncycastle.cms.CMSSignedData;
        import org.bouncycastle.cms.jcajce.JcaSignerInfoGeneratorBuilder;
        import org.bouncycastle.cms.jcajce.JcaSimpleSignerInfoVerifierBuilder;
        import org.bouncycastle.operator.jcajce.
        JcaDigestCalculatorProviderBuilder;
        import org.bouncycastle.cert.jcajce.JcaCertStore;
        import org.bouncycastle.asn1.x500.X500Name;
        public class BouncyCastleProbe {
        public static void main(String[] args) throws Exception {
        Security.addProvider(new BouncyCastleProvider());
        Class<?>[] classes={
        BouncyCastleProvider.class, org.bouncycastle.cms.CMSSignedData.class,
        org.bouncycastle.asn1.cms.ContentInfo.class}
        ;
        for(int i=0;
        i<classes.length;
        i++) if(!new File(classes[i].getProtectionDomain().getCodeSource().
        getLocation().toURI()).getCanonicalPath().equals(new File(args[i+1]).
        getCanonicalPath())) throw new
        Exception("Wrong Bouncy Castle class origin");
        if (!Security.getProvider("BC").getVersionStr().equals(args[0])) throw
        new Exception("Wrong provider version");
        KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA","BC");
        generator.initialize(2048);
        KeyPair key=generator.generateKeyPair();
        X500Name name=new X500Name("CN=runner-image-probe");
        long now=System.currentTimeMillis();
        var signer=new JcaContentSignerBuilder("SHA256withRSA").
        setProvider("BC").build(key.getPrivate());
        var builder=new JcaX509v3CertificateBuilder(name,BigInteger.ONE,new
        Date(now-1000),new Date(now+60000),name,key.getPublic());
        X509Certificate certificate=new JcaX509CertificateConverter().
        setProvider("BC").getCertificate(builder.build(signer));
        certificate.verify(key.getPublic(),"BC");
        KeyStore store=KeyStore.getInstance("PKCS12","BC");
        store.load(null,null);
        store.setKeyEntry("probe",key.getPrivate(),"example-password".
        toCharArray(),new java.security.cert.Certificate[]{
        certificate}
        );
        ByteArrayOutputStream output=new ByteArrayOutputStream();
        store.store(output,"example-password".toCharArray());
        KeyStore restored=KeyStore.getInstance("PKCS12","BC");
        restored.load(new ByteArrayInputStream(output.toByteArray()),
        "example-password".toCharArray());
        if (!Arrays.equals(restored.getKey("probe","example-password".
        toCharArray()).getEncoded(),key.getPrivate().getEncoded())) throw new
        Exception("Key material mismatch");
        try {
        KeyStore.getInstance("PKCS12","BC").load(new
        ByteArrayInputStream(output.toByteArray()),"wrong-password".
        toCharArray());
        throw new IllegalStateException("Bad password accepted");
        }
        catch(IOException expected) {
        }
        byte[] clear="runner-image-probe".getBytes(StandardCharsets.UTF_8);
        var cms=new CMSSignedDataGenerator();
        cms.addSignerInfoGenerator(new JcaSignerInfoGeneratorBuilder(new
        JcaDigestCalculatorProviderBuilder().setProvider("BC").build()).
        build(signer,certificate));
        cms.addCertificates(new JcaCertStore(List.of(certificate)));
        CMSSignedData signed=cms.generate(new CMSProcessableByteArray(clear),
        true);
        for(var info:signed.getSignerInfos().getSigners()) if(!info.verify(new
        JcaSimpleSignerInfoVerifierBuilder().setProvider("BC").
        build(certificate))) throw new Exception("CMS verification failed");
        byte[] tampered=clear.clone();
        tampered[0]^=1;
        CMSSignedData altered=new CMSSignedData(new
        CMSProcessableByteArray(tampered),cms.generate(new
        CMSProcessableByteArray(clear),false).getEncoded());
        boolean rejected=false;
        try {
        for(var info:altered.getSignerInfos().getSigners()) if(!info.
        verify(new JcaSimpleSignerInfoVerifierBuilder().setProvider("BC").
        build(certificate))) rejected=true;
        }
        catch(org.bouncycastle.cms.CMSException expected) {
        rejected=true;
        }
        if(!rejected) throw new Exception("Tampered CMS accepted");
        System.out.
        println("PKCS12 and CMS roundtrip and tamper rejection passed");
        }
        }
        """
        )
        classpath = os.pathsep.join(str(p.resolve()) for p in paths.values())
        subprocess.run(
            ["javac", "-cp", classpath, str(source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        crypto = subprocess.run(
            [
                "java",
                "-cp",
                str(work) + os.pathsep + classpath,
                "BouncyCastleProbe",
                version,
                *(str(p.resolve()) for p in paths.values()),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if "tamper rejection passed" not in crypto.stdout:
            raise ValueError("crypto consumer proof is incomplete")
        apk = work / "probe.apk"
        with zipfile.ZipFile(apk, "w") as archive:
            archive.writestr(
                "AndroidManifest.xml", '<manifest package="com.example.probe"/>'
            )
            archive.writestr("assets/probe.txt", "runner-image-probe")
        environment = {
            **os.environ,
            "ANDROID_USER_HOME": str(work / "android-user"),
            "ANDROID_SDK_ROOT": str(root.parent.parent),
        }
        analysis = subprocess.run(
            [str(root / "bin/apkanalyzer"), "files", "list", str(apk)],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if (
            "/assets/probe.txt" not in analysis.stdout
            or "/AndroidManifest.xml" not in analysis.stdout
        ):
            raise ValueError("actual APK analyzer did not enumerate the fixture")
        return {
            "crypto": crypto.stdout.strip(),
            "apkanalyzer": "fixture entries verified",
        }


def verify(root, *, execute=True):
    receipt = json.loads((root / "lib/runner-images-bc.json").read_text())
    paths = files(root)
    consumers = classpaths(root, paths)
    observed = {
        str(path.relative_to(root)): sha(path) for path in (*paths.values(), *consumers)
    }
    if receipt.get("schema") != 1 or receipt.get("files") != observed:
        raise ValueError("Android BC receipt does not match installed dependencies")
    if set(receipt.get("inputs", {})) != set(paths):
        raise ValueError("Android BC dependency closure differs from receipt")
    versions = {v["version"] for v in receipt["inputs"].values()}
    if len(versions) != 1:
        raise ValueError("Android BC receipt has mixed releases")
    version = next(iter(versions))
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version) or tuple(
        map(int, version.split("."))
    ) < (1, 85):
        raise ValueError("Android BC receipt predates the security fixes")
    for name, path in paths.items():
        if sha(path) != receipt["inputs"][name]["sha256"]:
            raise ValueError("Android BC receipt does not match upstream bytes")
    if execute:
        receipt["checks"] = probe(root, paths, next(iter(versions)))
    return receipt


def refresh(root):
    paths = files(root)
    consumers = classpaths(root, paths)
    report = root / "lib/runner-images-bc.json"
    if report.exists():
        previous = verify(root)
    else:
        previous = None
    selected = resolve()
    if previous and previous["inputs"] == selected:
        return previous
    with tempfile.TemporaryDirectory(prefix="runner-image-bc-stage-") as directory:
        work = Path(directory)
        for name, spec in selected.items():
            target = work / (name + ".jar")
            target.write_bytes(fetch(spec["url"]))
            if sha(target) != spec["sha256"]:
                raise ValueError("official Android BC JAR checksum mismatch")
            with zipfile.ZipFile(target) as jar:
                manifest = jar.read("META-INF/MANIFEST.MF").decode()
                if "Bundle-Version: " + spec["version"] not in manifest.splitlines():
                    raise ValueError("BC manifest differs from selected release")
        candidates = {
            name: root
            / "lib/external/org/bouncycastle"
            / name
            / spec["version"]
            / (name + "-" + spec["version"] + ".jar")
            for name, spec in selected.items()
        }
        original = {
            path: (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in (*paths.values(), *consumers)
        }
        for path in candidates.values():
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("new Android dependency escapes its tool root")
            if path not in original and path.exists():
                raise ValueError("refusing to replace an unowned candidate path")
        old_report = report.read_bytes() if report.exists() else None
        try:
            for name, path in candidates.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((work / (name + ".jar")).read_bytes())
                path.chmod(original[paths[name]][1])
            for path, text in consumers.items():
                for name, previous_path in paths.items():
                    text = text.replace(
                        str(previous_path.relative_to(root / "lib")),
                        str(candidates[name].relative_to(root / "lib")),
                    )
                path.write_bytes(rewrite_classpath(original[path][0], text))
            classpaths(root, candidates)
            checks = probe(root, candidates, next(iter(selected.values()))["version"])
            for path in paths.values():
                if path not in candidates.values():
                    path.unlink()
            receipt = {
                "schema": 1,
                "inputs": selected,
                "checks": checks,
                "files": {
                    str(p.relative_to(root)): sha(p)
                    for p in (*candidates.values(), *consumers)
                },
            }
            staged_report = work / "report.json"
            staged_report.write_text(json.dumps(receipt, indent=2) + "\n")
            report.write_bytes(staged_report.read_bytes())
            verify(root, execute=False)
        except BaseException:
            for path, (data, mode) in original.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(mode)
            for path in candidates.values():
                if path not in original:
                    path.unlink(missing_ok=True)
            if old_report is None:
                report.unlink(missing_ok=True)
            else:
                report.write_bytes(old_report)
            raise
        return receipt


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if mode not in ("refresh", "verify"):
        raise ValueError("expected refresh or verify")
    root = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path.home() / "android-sdk/cmdline-tools/latest"
    ).resolve()
    receipt = refresh(root) if mode == "refresh" else verify(root)
    print(
        json.dumps(
            {
                "android_bc_dependencies": receipt,
                "android_bc_receipt_sha256": sha(root / "lib/runner-images-bc.json"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
