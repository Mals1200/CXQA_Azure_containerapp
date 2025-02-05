# Azure Container App with Ask_Question Function

## Overview

This application hosts a Flask API that exposes an endpoint to process questions using the `Ask_Question()` function defined in `ask_func.py`. It is containerized using Docker and deployed on Azure.

## Repository Structure









curl command on windows:
'''
curl -X POST "https://cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io/ask" -H "Content-Type: application/json" -d "{\"question\": \"<Question>\"}"
'''
