# Symphony on K8s Hostfactory Provider

This provider extends
[IBM Symphony hostfactory custom provider](https://www.ibm.com/docs/en/spectrum-symphony/7.3.2?topic=factory-provider-plug-in-interface-specification)
to provide the necessary functionality for hostfactory to provision
Symphony compute nodes on Kubernetes.


## Overview

IBM Symphony hostfactory is a service that runs as part of IBM Symphony Orchestrator.
The hosfactory service manages compute host bursting to public cloud. Hostfactory provides custom provider extension to allow usecase specific implementation of the host provider.
[Hostfactory overview](https://www.ibm.com/docs/en/spectrum-symphony/7.3.2?topic=factory-overview).


### Prerequisites

The plugin should be installed as part of an IBM Symphony deployment with
the necessary credentials/capabilities to deploy pods on a Kubernetes cluster.


### Provider installation

The provider scripts `k8s-hf` should be part of the `${HF_CONFDIR}` under Symphony and [hostProviderPlugins.json](https://www.ibm.com/docs/en/spectrum-symphony/7.3.2?topic=factory-hostproviderpluginsjson) should add the plugin name:
```
{
    "version": 2,
    "providerplugins": [
        {
            "name": "k8s-hf",
            "enabled": 1,
            "scriptPath": "${HF_CONFDIR}/providers/k8s-hf/scripts/"
        }
    ]
}
```

as for the script files and
[hostProviders.json](https://www.ibm.com/docs/en/spectrum-symphony/7.3.2?topic=factory-hostprovidersjson)
they should be copied under `${HF_CONFDIR}` in the following tree structure
```
├── providers
│   ├── hostProviders.json
│   └── k8s-hf
│       └── scripts
│           ├── getAvailableTemplates.sh
│           ├── getRequestStatus.sh
│           ├── getReturnRequests.sh
│           ├── requestMachines.sh
│           └── requestReturnMachines.sh
```

built python binaries should be part of `$PATH` in the runtime environment.

### Provider mechanism

The Symphony on K8s provider implements multiple processes to allow the interface to invoke Kubernetes API for Pod/Host management asynchronously. The process that should be running as part of the runtime environment:

- `hostfactory watch request-machines`
Watches hostfactory provider requests and creates the pods.
- `hostfactory watch request-return-machines`
Watches hostfactory provider return requests and deletes the pods.
- `hostfactory watch pods`
Watches the kubernetes event loop for new/modified/deleted pods and captures them on disk.


## Feedback

Please contact hpc-symphony-k8s@morganstanley.com for any questions.