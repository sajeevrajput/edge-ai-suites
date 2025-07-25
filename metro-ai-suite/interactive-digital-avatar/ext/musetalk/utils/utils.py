import os
import cv2
import numpy as np
import torch

from ..whisper.audio2feature import Audio2Feature
from ..models.vae import VAE
from ..models.unet import UNet,PositionalEncoding

def load_all_model():
    audio_processor = Audio2Feature(model_path="resource/musetalk_models/whisper/tiny.pt")
    vae = VAE(model_path="resource/musetalk_models/sd-vae-ft-mse/")
    unet = UNet(unet_config="resource/musetalk_models/musetalk/musetalk.json",
                model_path="resource/musetalk_models/musetalk/pytorch_model.bin")
    pe = PositionalEncoding(d_model=384)
    return audio_processor,vae,unet,pe

def get_file_type(video_path):
    _, ext = os.path.splitext(video_path)

    if ext.lower() in ['.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff']:
        return 'image'
    elif ext.lower() in ['.avi', '.mp4', '.mov', '.flv', '.mkv']:
        return 'video'
    else:
        return 'unsupported'

def get_video_fps(video_path):
    video = cv2.VideoCapture(video_path)
    fps = video.get(cv2.CAP_PROP_FPS)
    video.release()
    return fps

def datagen(whisper_chunks,
            vae_encode_latents,
            batch_size=8,
            delay_frame=0):
    whisper_batch, latent_batch = [], []
    for i, w in enumerate(whisper_chunks):
        idx = (i+delay_frame)%len(vae_encode_latents)
        latent = vae_encode_latents[idx]
        whisper_batch.append(w)
        latent_batch.append(latent)

        if len(latent_batch) >= batch_size:
            whisper_batch = np.stack(whisper_batch)
            latent_batch = torch.cat(latent_batch, dim=0)
            yield whisper_batch, latent_batch
            whisper_batch, latent_batch = [], []

    # the last batch may smaller than batch size
    if len(latent_batch) > 0:
        whisper_batch = np.stack(whisper_batch)
        latent_batch = torch.cat(latent_batch, dim=0)

        yield whisper_batch, latent_batch
