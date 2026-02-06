# MLOps using Model Downloader

Applications for industrial vision can also be used to demonstrate MLOps workflow using Model Downloader microservice.
With this feature, during runtime, you can download a new model using the microservice and restart the pipeline with the new model.

>To simplify this demonstration, we assume that models have already been downloaded to an accessible location (`/tmp/models`) using the Model Downloader from a running Geti server before restarting the pipeline.

## Contents

### Pre-requisites
>NOTE: Model Download service has already downloaded the model to be updated to `/tmp/models`


### Steps
1. Set up the sample application to start a pipeline. A pipeline named `pallet_defect_detection_mlops` is already provided in the `pipeline-server-config.json` for this demonstration with the pallet defect detection sample app.

   > Ensure that the pipeline inference element such as gvadetect/gvaclassify/gvainference should not have a `model-instance-id` property set. If set, this would not allow the new model to be run with the same value provided in the `model-instance-id`.

   Navigate to the `[WORKDIR]/edge-ai-suites/manufacturing-ai-suite/industrial-edge-insights-vision` directory and set up the app.

   ```sh
   cp .env_pallet_defect_detection .env
   ```

2. Update the following variables in `.env` file.

   ``` sh
   HOST_IP= # <IP Adress of the host machine>

   MTX_WEBRTCICESERVERS2_0_USERNAME=  # Webrtc-mediamtx username. e.g intel1234
   MTX_WEBRTCICESERVERS2_0_PASSWORD=  # Webrtc-mediamtx password. e.g intel1234
   ```

3. Run the setup script using the following command.

   ```sh
   ./setup.sh
   ```

4. Bring up the containers

   ```sh
   docker compose up -d
   ```

5. Check to see if the pipeline is loaded is present which in our case is `pallet_defect_detection_mlops`.

   ```sh
   ./sample_list.sh
   ```

6. Modify the payload in `apps/pallet-defect-detection/payload.json` to launch an instance for the mlops pipeline.

   ```json
   [
       {
           "pipeline": "pallet_defect_detection_mlops",
           "payload":{
               "source": {
                   "uri": "file:///home/pipeline-server/resources/videos/warehouse.avi",
                   "type": "uri"
               },
               "destination": {
               "frame": {
                   "type": "webrtc",
                   "peer-id": "pdd"
               }
               },
               "parameters": {
                   "detection-properties": {
                       "model": "/home/pipeline-server/resources/models/pallet-defect-detection/deployment/Detection/model/model.xml",
                       "device": "CPU"
                   }
               }
           }
       }
   ]
   ```

7. Start the pipeline with the above payload.

   ```bash
   ./sample_start.sh -p pallet_defect_detection_mlops
   ```
   Note the instance-id of the pipeline launched.

8. Verify the pipeline is running. You can View the WebRTC streaming on `http://<HOST_IP>:<mediamtx-port>/<peer-str-id>` by replacing `<peer-str-id>` with the value used in the original cURL command to start the pipeline.

   ![WebRTC streaming](./images/webrtc-streaming.png)

### Downloading model with Model Downloader 

At this point, user would like to restart the pipeline with a newer model. The new model can a retrained version of the existing model or a different model altogether. We use Model Downloader microservice to help download the model. It supports downloading  public models as well as geti models from a running Geti server.

For our demonstration, we will assume the pallet defect detection model has been retrained and is available to be downloaded from the Geti server using the Model Downloader service. Also, the downloaded location is accessible by the dlstreamer pipeline server.
We will assume model has been downloaded to /tmp/tmp-models directory. `/tmp`dir is already accessible by the sample application. If not, please add it to the `volumes` section of docker-compose file.


9. Stop the running pipeline by using the pipeline instance "id".

   ```sh
   curl -k --location -X DELETE https://<HOST_IP>/api/pipelines/{instance_id}
   ```
4. Start a new pipeline with this new model. Before that modify the payload.json to use this new model in `apps/pallet-defect-detection/payload.json`

   ```json
   [
       {
           "pipeline": "pallet_defect_detection_mlops",
           "payload":{
               "source": {
                   "uri": "file:///home/pipeline-server/resources/videos/warehouse.avi",
                   "type": "uri"
               },
               "destination": {
               "frame": {
                   "type": "webrtc",
                   "peer-id": "pdd-new"
               }
               },
               "parameters": {
                   "detection-properties": {
                       "model": "/tmp/models/pallet-defect-detection/deployment/Detection/model/model.xml",
                       "device": "CPU"
                   }
               }
           }
       }
   ]
   ```

5. View the WebRTC streaming on `http://<HOST_IP>:<mediamtx-port>/<peer-str-id>` by replacing `<peer-str-id>` with the value used in the original cURL command to start the pipeline.

   ![WebRTC streaming](./images/webrtc-streaming.png)

## Additional resources
### Downloading models from Model Downloader
To learn how to setup and downloader models from Geti server, see [here](https://github.com/open-edge-platform/edge-ai-libraries/blob/main/microservices/model-download/docs/user-guide/get-started.md#quick-start)

1. **Clone the Repository**:
    - Clone the model-download repository:
      ```bash
      # Clone the latest on mainline
        git clone https://github.com/open-edge-platform/edge-ai-libraries.git edge-ai-libraries
      # Alternatively, Clone a specific release branch
        git clone https://github.com/open-edge-platform/edge-ai-libraries.git edge-ai-libraries -b <release-tag>
      ```
2. **Navigate to the directory**:
    - Go to the model-download microservice directory
      ```bash
      cd edge-ai-libraries/microservices/model-download
      ```
3. **Configure the environment variables**
    - Set the below environment variables
      ```bash
      export REGISTRY="intel/"
      export TAG=1.0.1

      export GETI_HOST=<GETI_HOST_ADDRESS>
      export GETI_ORGANIZATION_ID=<YOUR_GETI_ORGANIZATION_ID>
      export GETI_WORKSPACE_ID=<YOUR_GETI_WORKSPACE_ID>
      export GETI_TOKEN=<GETI_ACCESS_TOKEN>
      export GETI_SERVER_API_VERSION=v1
      export GETI_SERVER_SSL_VERIFY=False  #DEFAULT is FALSE
      ```


4. **Launch the service**
    - Use the run script to start the service and enable the plugins
      ```bash
      source scripts/run_service.sh up --plugins geti,ultralytics,huggingface --model-path /tmp/models # there is a bug that would not allow only ultralytics plugin to install
   
   >NOTE The --model-path location is where models are downloaded. In this case, its /tmp/models. Ensure that DLSPS has access to this path. You can check the volumes section of DLSPS compose file fo this.

1. Download the model.

   ```sh
   export HOST_IP=<HOST_IP_ADDRESS>
   curl --location 'http://$HOST_IP:8200/api/v1/models/download?download_path=openvino_folder' \
   --header 'Content-Type: application/json' \
   --data '{
      "models": [
         {
               "name": "yolox-tiny",
               "hub": "geti",
               "type": "vision",
               "precision": "int8",
               "model_group_id": "691bfb86c7b9a6d48b162af3",
               "project_id": "691bfa6c0a9b332eadf1d28c",
               "export_type": "optimized"
         }
      ],
      "parallel_downloads": true
   }'
   ```
   >NOTE The above command returns a job id. Note it so that you may use it to check download status. See below
2. Run the following curl command to check for model download status. Depending upon the speed and size of the model, you may have to wait for longer duration

   ```sh
   curl -X GET "http://$HOST_IP:8200/api/v1/jobs/<job_id>"
   ```

   > **Note:**: The model is already available in /tmp/models. Check if the /tmp is accessible to DLSPS container. If not, please add it to volumes section of DLSPS in compose file, and restart the DLSPS service.
